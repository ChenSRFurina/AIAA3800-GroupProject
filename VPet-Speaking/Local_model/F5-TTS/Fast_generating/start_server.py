"""
F5-TTS 常驻服务端：加载模型 + 学习完整 ref 音色，缓存到 temp/，推理复用。

窗口 1（不要关）:
    python start_server.py --device cuda

默认音质: nfe=8, cfg=2.0（完整参考音，不截断）
强制重建音色缓存:
    python start_server.py --device cuda --rebuild_ref
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import logging
import socket
import struct
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
import torch
import torchaudio
from cached_path import cached_path
from hydra.utils import get_class
from omegaconf import OmegaConf


HERE = Path(__file__).resolve().parent
F5_ROOT = HERE.parent
SRC = F5_ROOT / "src"
TEMP_DIR = HERE / "temp"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from f5_tts.infer.utils_infer import (  # noqa: E402
    hop_length,
    load_model,
    load_vocoder,
    preprocess_ref_audio_text,
    target_rms,
    target_sample_rate,
)
from f5_tts.model.utils import convert_char_to_pinyin, seed_everything  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("f5_fast_server")


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_device(preferred: str | None = None, cuda_id: int = 0) -> str:
    if preferred:
        pref = preferred.strip().lower()
        if pref.startswith("cuda"):
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "指定了 GPU 推理，但未检测到 CUDA。\n"
                    "请在 F5TTS 环境中安装 CUDA 版 PyTorch，例如:\n"
                    "  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124"
                )
            if ":" in pref:
                return pref
            return f"cuda:{cuda_id}"
        return preferred

    if torch.cuda.is_available():
        return f"cuda:{cuda_id}"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def setup_cuda(device: str) -> None:
    if not device.startswith("cuda"):
        return
    idx = 0
    if ":" in device:
        idx = int(device.split(":", 1)[1])
    torch.cuda.set_device(idx)
    torch.backends.cudnn.benchmark = True
    torch.set_float32_matmul_precision("high")
    name = torch.cuda.get_device_name(idx)
    cap = torch.cuda.get_device_capability(idx)
    logger.info("GPU: %s (cuda:%s, compute %s.%s)", name, idx, cap[0], cap[1])


def pick_autocast_dtype(device: str) -> torch.dtype:
    if not device.startswith("cuda"):
        return torch.float32
    major, _ = torch.cuda.get_device_capability(int(device.split(":")[-1]) if ":" in device else 0)
    if major >= 8:
        return torch.bfloat16
    return torch.float16


def find_ref_pair(ref_dir: Path) -> tuple[Path, str]:
    """从 ref/ 目录找第一对 wav + 同名 txt。"""
    if not ref_dir.is_dir():
        raise FileNotFoundError(f"参考音色目录不存在: {ref_dir}")

    wavs = sorted(ref_dir.glob("*.wav")) + sorted(ref_dir.glob("*.mp3")) + sorted(ref_dir.glob("*.flac"))
    if not wavs:
        raise FileNotFoundError(f"在 {ref_dir} 未找到参考音频 (*.wav/*.mp3/*.flac)")

    wav = wavs[0]
    txt = wav.with_suffix(".txt")
    if txt.is_file():
        ref_text = txt.read_text(encoding="utf-8").strip()
    else:
        ref_text = ""
        logger.warning("未找到 %s，将自动 ASR 转写参考音频", txt.name)

    return wav, ref_text


def recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("连接已断开")
        buf.extend(chunk)
    return bytes(buf)


def recv_json(conn: socket.socket) -> dict:
    header = recv_exact(conn, 4)
    (length,) = struct.unpack("<I", header)
    if length == 0 or length > 8 * 1024 * 1024:
        raise ValueError(f"非法 JSON 长度: {length}")
    payload = recv_exact(conn, length)
    return json.loads(payload.decode("utf-8"))


def send_json(conn: socket.socket, obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    conn.sendall(struct.pack("<I", len(raw)) + raw)


def send_json_and_bytes(conn: socket.socket, meta: dict, data: bytes) -> None:
    send_json(conn, meta)
    conn.sendall(struct.pack("<I", len(data)) + data)


class FastTTSServer:
    def __init__(
        self,
        model_name: str,
        ckpt_file: str,
        vocab_file: str,
        ref_dir: Path,
        device: str,
        default_nfe_step: int,
        cfg_strength: float,
        sway_sampling_coef: float,
        speed: float,
        max_ref_sec: float = 0.0,
        use_compile: bool = False,
        temp_dir: Path | None = None,
        force_rebuild_ref: bool = False,
    ):
        self.device = device
        self.default_nfe_step = default_nfe_step
        self.cfg_strength = cfg_strength
        self.sway_sampling_coef = sway_sampling_coef
        self.speed = speed
        self.ref_dir = ref_dir
        self.max_ref_sec = max_ref_sec  # 0 = 不截断，保留完整 ref
        self.temp_dir = Path(temp_dir) if temp_dir else TEMP_DIR
        self.force_rebuild_ref = force_rebuild_ref
        self.lock = threading.Lock()
        self.autocast_dtype = pick_autocast_dtype(device)
        self.ref_rms = 1.0
        self.ref_audio_len = 0
        self.ref_text_byte_len = 1
        self.ref_mel: torch.Tensor | None = None  # 预计算 mel，推理跳过 mel_spec

        setup_cuda(device)
        if device.startswith("cuda"):
            logger.info("推理精度: %s (autocast)", self.autocast_dtype)

        model_cfg = OmegaConf.load(str(SRC / "f5_tts" / "configs" / f"{model_name}.yaml"))
        model_cls = get_class(f"f5_tts.model.{model_cfg.model.backbone}")
        model_arc = model_cfg.model.arch
        self.mel_spec_type = model_cfg.model.mel_spec.mel_spec_type
        self.sample_rate = model_cfg.model.mel_spec.target_sample_rate

        if not ckpt_file:
            local_ckpt = F5_ROOT.parent / "model" / model_name
            candidates = [
                local_ckpt / "model_1250000.safetensors",
                local_ckpt / "model_1200000.safetensors",
                local_ckpt / "model_1250000.pt",
                local_ckpt / "model_1200000.pt",
            ]
            for c in candidates:
                if c.is_file():
                    ckpt_file = str(c)
                    break
            if not ckpt_file:
                if model_name == "F5TTS_v1_Base":
                    ckpt_file = str(cached_path("hf://SWivid/F5-TTS/F5TTS_v1_Base/model_1250000.safetensors"))
                elif model_name == "F5TTS_Base":
                    ckpt_file = str(cached_path("hf://SWivid/F5-TTS/F5TTS_Base/model_1200000.safetensors"))
                else:
                    raise ValueError(f"未指定 ckpt，且不支持自动下载的模型: {model_name}")

        logger.info("设备: %s", self.device)
        logger.info("加载模型: %s", model_name)
        logger.info("权重: %s", ckpt_file)
        logger.info(
            "音质参数: nfe=%s cfg=%s max_ref=%s compile=%s temp=%s",
            default_nfe_step,
            cfg_strength,
            "完整" if max_ref_sec <= 0 else f"{max_ref_sec:.1f}s",
            use_compile,
            self.temp_dir,
        )

        self.model = load_model(
            model_cls,
            model_arc,
            ckpt_path=ckpt_file,
            mel_spec_type=self.mel_spec_type,
            vocab_file=vocab_file,
            ode_method="euler",
            use_ema=True,
            device=self.device,
        )
        self.vocoder = load_vocoder(
            vocoder_name=self.mel_spec_type,
            is_local=False,
            local_path="",
            device=self.device,
        )
        self.model.eval()
        self.vocoder.eval()

        self.ref_audio_path: str | None = None
        self.ref_text: str = ""
        self.ref_audio_tensor: torch.Tensor | None = None
        self.ref_sr: int = self.sample_rate

        self.load_reference()
        if use_compile and self.device.startswith("cuda") and hasattr(torch, "compile"):
            self._try_compile()
        self.warmup()

    def _try_compile(self) -> None:
        try:
            logger.info("尝试 torch.compile 加速（首次会稍慢）...")
            self.model = torch.compile(self.model, mode="reduce-overhead")
            logger.info("torch.compile 已启用")
        except Exception as e:
            logger.warning("torch.compile 失败，继续普通模式: %s", e)

    def _cache_dir_for(self, wav_path: Path) -> Path:
        key = file_md5(wav_path)[:16]
        return self.temp_dir / f"ref_{wav_path.stem}_{key}"

    def _try_load_ref_cache(self, cache_dir: Path, src_hash: str) -> bool:
        meta_path = cache_dir / "meta.json"
        wave_path = cache_dir / "ref_wave.pt"
        mel_path = cache_dir / "ref_mel.pt"
        text_path = cache_dir / "ref_text.txt"
        if not (meta_path.is_file() and wave_path.is_file() and mel_path.is_file() and text_path.is_file()):
            return False
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("src_hash") != src_hash:
            return False
        if meta.get("sample_rate") != target_sample_rate:
            return False

        logger.info("命中 temp 音色缓存: %s", cache_dir.name)
        wave = torch.load(wave_path, map_location="cpu", weights_only=True)
        mel = torch.load(mel_path, map_location="cpu", weights_only=True)
        text = text_path.read_text(encoding="utf-8")

        self.ref_text = text
        self.ref_rms = float(meta.get("ref_rms", 1.0))
        self.ref_sr = target_sample_rate
        self.ref_audio_path = str(cache_dir / "processed.wav")
        self.ref_audio_tensor = wave.to(self.device, non_blocking=True).contiguous()
        self.ref_mel = mel.to(self.device, non_blocking=True).contiguous()
        self.ref_audio_len = int(self.ref_mel.shape[1])
        self.ref_text_byte_len = max(1, len(self.ref_text.encode("utf-8")))
        return True

    def _save_ref_cache(
        self,
        cache_dir: Path,
        src_hash: str,
        wav_path: Path,
        processed_wav: Path,
        audio: torch.Tensor,
        mel: torch.Tensor,
        text: str,
    ) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest_wav = cache_dir / "processed.wav"
        if processed_wav.is_file():
            dest_wav.write_bytes(processed_wav.read_bytes())
        else:
            torchaudio.save(str(dest_wav), audio.cpu(), target_sample_rate)

        torch.save(audio.cpu(), cache_dir / "ref_wave.pt")
        torch.save(mel.cpu(), cache_dir / "ref_mel.pt")
        (cache_dir / "ref_text.txt").write_text(text, encoding="utf-8")
        meta = {
            "src_hash": src_hash,
            "src_path": str(wav_path),
            "sample_rate": target_sample_rate,
            "ref_rms": self.ref_rms,
            "ref_audio_len": int(mel.shape[1]),
            "duration_sec": round(audio.shape[-1] / target_sample_rate, 3),
            "text": text,
        }
        (cache_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已写入 temp 音色缓存: %s", cache_dir)

    @torch.inference_mode()
    def _compute_ref_mel(self, audio: torch.Tensor) -> torch.Tensor:
        """启动时用模型 mel_spec 预计算参考音色 mel，推理时直接喂给 sample。"""
        wav = audio.to(self.device)
        mel = self.model.mel_spec(wav)  # [B, n_mel, T]
        mel = mel.permute(0, 2, 1).contiguous()  # [B, T, n_mel]
        return mel

    def load_reference(self) -> None:
        wav_path, ref_text = find_ref_pair(self.ref_dir)
        src_hash = file_md5(wav_path)
        cache_dir = self._cache_dir_for(wav_path)
        logger.info("学习参考音色: %s", wav_path.name)

        if not self.force_rebuild_ref and self._try_load_ref_cache(cache_dir, src_hash):
            logger.info("参考文本: %s", self.ref_text.strip())
            logger.info(
                "参考音频(缓存): %.2fs | mel_frames=%s | device=%s",
                self.ref_audio_tensor.shape[-1] / self.ref_sr if self.ref_audio_tensor is not None else 0,
                self.ref_audio_len,
                self.device,
            )
            return

        logger.info("未命中缓存，开始完整预处理并写入 temp/ ...")
        processed_audio, processed_text = preprocess_ref_audio_text(str(wav_path), ref_text)
        audio, sr = torchaudio.load(processed_audio)

        if audio.shape[0] > 1:
            audio = torch.mean(audio, dim=0, keepdim=True)

        if sr != target_sample_rate:
            audio = torchaudio.transforms.Resample(sr, target_sample_rate)(audio)
            sr = target_sample_rate

        # 默认不截断；仅当显式 --max_ref_sec > 0 时截短
        if self.max_ref_sec > 0:
            max_samples = int(self.max_ref_sec * target_sample_rate)
            if audio.shape[-1] > max_samples:
                audio = audio[:, :max_samples]
                logger.info("参考音频已截短到 %.1fs", self.max_ref_sec)

        rms = torch.sqrt(torch.mean(torch.square(audio)))
        self.ref_rms = float(rms.item()) if float(rms.item()) > 0 else 1.0
        if self.ref_rms < target_rms:
            audio = audio * (target_rms / self.ref_rms)

        if not processed_text.endswith(". ") and not processed_text.endswith("。"):
            processed_text = processed_text.rstrip(".。") + ". "

        self.ref_audio_path = processed_audio
        self.ref_text = processed_text
        self.ref_audio_tensor = audio.to(self.device, non_blocking=True).contiguous()
        self.ref_sr = sr

        logger.info("预计算参考音色 mel（完整 ref）...")
        self.ref_mel = self._compute_ref_mel(self.ref_audio_tensor)
        self.ref_audio_len = int(self.ref_mel.shape[1])
        self.ref_text_byte_len = max(1, len(self.ref_text.encode("utf-8")))

        self._save_ref_cache(
            cache_dir,
            src_hash,
            wav_path,
            Path(processed_audio),
            self.ref_audio_tensor.detach().cpu(),
            self.ref_mel.detach().cpu(),
            self.ref_text,
        )

        logger.info("参考文本: %s", self.ref_text.strip())
        logger.info(
            "参考音频: %.2fs | mel_frames=%s | 已缓存到 %s",
            self.ref_audio_tensor.shape[-1] / sr,
            self.ref_audio_len,
            cache_dir,
        )

    def warmup(self, text: str = "你好，这是一次预热。") -> None:
        logger.info("GPU 预热中...")
        t0 = time.perf_counter()
        self.synthesize(text, nfe_step=self.default_nfe_step, seed=0)
        self.synthesize(text, nfe_step=self.default_nfe_step, seed=0)
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        logger.info("预热完成，耗时 %.3fs", time.perf_counter() - t0)

    def _fast_infer(self, text: str, nfe: int, spd: float, cfg: float) -> tuple[np.ndarray, int]:
        """使用启动时缓存的 ref mel，跳过每轮 mel_spec / 预处理。"""
        assert self.ref_mel is not None or self.ref_audio_tensor is not None

        local_speed = max(0.5, float(spd))
        text_list = [self.ref_text + text]
        final_text_list = convert_char_to_pinyin(text_list)

        gen_text_len = len(text.encode("utf-8"))
        duration = self.ref_audio_len + int(
            self.ref_audio_len / self.ref_text_byte_len * gen_text_len / local_speed
        )

        # ndim==3 的 mel 会让 CFM.sample 跳过 mel_spec，直接用缓存音色条件
        cond = self.ref_mel if self.ref_mel is not None else self.ref_audio_tensor

        generated, _ = self.model.sample(
            cond=cond,
            text=final_text_list,
            duration=duration,
            steps=nfe,
            cfg_strength=cfg,
            sway_sampling_coef=self.sway_sampling_coef,
            use_epss=True,
        )
        generated = generated.to(torch.float32)
        generated = generated[:, self.ref_audio_len :, :].permute(0, 2, 1)

        if self.mel_spec_type == "vocos":
            wave = self.vocoder.decode(generated)
        else:
            wave = self.vocoder(generated)

        if self.ref_rms < target_rms:
            wave = wave * (self.ref_rms / target_rms)

        return wave.squeeze().detach().float().cpu().numpy(), target_sample_rate

    def synthesize(
        self,
        text: str,
        nfe_step: int | None = None,
        seed: int | None = None,
        speed: float | None = None,
        cfg_strength: float | None = None,
    ) -> tuple[np.ndarray, int, float]:
        text = (text or "").strip()
        if not text:
            raise ValueError("生成文本为空")

        nfe = int(nfe_step or self.default_nfe_step)
        spd = float(self.speed if speed is None else speed)
        cfg = float(self.cfg_strength if cfg_strength is None else cfg_strength)

        if seed is not None:
            seed_everything(int(seed))

        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        t0 = time.perf_counter()

        with self.lock, torch.inference_mode():
            if self.device.startswith("cuda"):
                with torch.autocast(device_type="cuda", dtype=self.autocast_dtype):
                    wave, sr = self._fast_infer(text, nfe, spd, cfg)
            else:
                wave, sr = self._fast_infer(text, nfe, spd, cfg)

        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        infer_s = time.perf_counter() - t0

        if wave is None or (hasattr(wave, "size") and wave.size == 0):
            raise RuntimeError("合成失败，无音频输出")
        return np.asarray(wave, dtype=np.float32), int(sr), infer_s


def handle_client(conn: socket.socket, addr, server: FastTTSServer) -> None:
    logger.info("客户端连接: %s", addr)
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        while True:
            try:
                req = recv_json(conn)
            except ConnectionError:
                break

            cmd = (req.get("cmd") or "gen").lower()
            try:
                if cmd in ("ping", "health"):
                    send_json(
                        conn,
                        {
                            "ok": True,
                            "cmd": "pong",
                            "device": server.device,
                            "autocast_dtype": str(server.autocast_dtype).replace("torch.", ""),
                            "gpu_name": (
                                torch.cuda.get_device_name(int(server.device.split(":")[-1]))
                                if server.device.startswith("cuda")
                                else None
                            ),
                            "sample_rate": server.sample_rate,
                            "ref_text": server.ref_text.strip(),
                            "default_nfe_step": server.default_nfe_step,
                            "cfg_strength": server.cfg_strength,
                            "ref_cached": server.ref_mel is not None,
                        },
                    )
                    continue

                if cmd in ("reload_ref", "reload"):
                    server.force_rebuild_ref = True
                    server.load_reference()
                    server.force_rebuild_ref = False
                    server.warmup()
                    send_json(conn, {"ok": True, "cmd": "reload_ref", "ref_text": server.ref_text.strip()})
                    continue

                if cmd not in ("gen", "generate", "tts"):
                    send_json(conn, {"ok": False, "error": f"未知命令: {cmd}"})
                    continue

                wave, sr, infer_s = server.synthesize(
                    text=req.get("text", ""),
                    nfe_step=req.get("nfe_step"),
                    seed=req.get("seed"),
                    speed=req.get("speed"),
                    cfg_strength=req.get("cfg_strength"),
                )
                pcm = wave.astype(np.float32, copy=False).tobytes()
                send_json_and_bytes(
                    conn,
                    {
                        "ok": True,
                        "cmd": "gen",
                        "sr": sr,
                        "dtype": "float32",
                        "samples": int(wave.size),
                        "infer_ms": round(infer_s * 1000.0, 2),
                        "nfe_step": int(req.get("nfe_step") or server.default_nfe_step),
                        "text": req.get("text", ""),
                    },
                    pcm,
                )
                logger.info(
                    "合成完成 | %.1f ms%s | nfe=%s cfg=%s | text=%s",
                    infer_s * 1000.0,
                    " OK(<0.3s)" if infer_s <= 0.3 else "",
                    req.get("nfe_step") or server.default_nfe_step,
                    req.get("cfg_strength") if req.get("cfg_strength") is not None else server.cfg_strength,
                    (req.get("text") or "")[:40],
                )
            except Exception as e:
                logger.error("处理请求失败: %s", e)
                traceback.print_exc()
                send_json(conn, {"ok": False, "error": str(e)})
    finally:
        try:
            conn.close()
        except OSError:
            pass
        logger.info("客户端断开: %s", addr)


def serve(host: str, port: int, server: FastTTSServer) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(8)
        logger.info("服务已启动: %s:%s  (保持此窗口运行)", host, port)
        logger.info("另一个窗口运行: python fast_gen.py \"你好啊\"")
        while True:
            conn, addr = sock.accept()
            threading.Thread(target=handle_client, args=(conn, addr, server), daemon=True).start()


def parse_args():
    p = argparse.ArgumentParser(description="F5-TTS Fast Generating Server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--model", default="F5TTS_v1_Base")
    p.add_argument("--ckpt_file", default="", help="本地权重路径；为空则优先 Local_model/model，再 HuggingFace")
    p.add_argument("--vocab_file", default="")
    p.add_argument("--ref_dir", default=str(HERE / "ref"), help="参考音色目录（含 wav + txt）")
    p.add_argument("--temp_dir", default=str(TEMP_DIR), help="音色模仿缓存目录（默认 Fast_generating/temp）")
    p.add_argument("--rebuild_ref", action="store_true", help="强制重新预处理完整 ref 并覆盖 temp 缓存")
    p.add_argument("--device", default="cuda", help="推理设备，默认 cuda（GPU）")
    p.add_argument("--cuda_id", type=int, default=0, help="GPU 编号，多卡时指定")
    p.add_argument("--allow_cpu", action="store_true", help="允许无 GPU 时回退 CPU（默认必须用 GPU）")
    p.add_argument("--nfe_step", type=int, default=8, help="ODE 步数，默认 8（音质）")
    p.add_argument("--cfg_strength", type=float, default=2.0, help="CFG 强度，默认 2.0（音质）")
    p.add_argument("--sway_sampling_coef", type=float, default=-1.0)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument(
        "--max_ref_sec",
        type=float,
        default=0.0,
        help="参考音频最长秒数；0=不截断（完整 ref）。仅加速调试时再设为正数",
    )
    p.add_argument("--compile", action="store_true", help="启用 torch.compile（可选）")
    return p.parse_args()


def main():
    args = parse_args()

    try:
        device = resolve_device(args.device, args.cuda_id)
    except RuntimeError as e:
        if args.allow_cpu:
            logger.warning("%s — 回退 CPU", e)
            device = "cpu"
        else:
            raise

    if not args.allow_cpu and not device.startswith("cuda"):
        raise RuntimeError(
            f"当前设备为 {device}，未使用 GPU。\n"
            "请确认: 1) NVIDIA 驱动正常  2) F5TTS 环境安装了 CUDA 版 PyTorch\n"
            "启动: python start_server.py --device cuda --cuda_id 0\n"
            "调试: python start_server.py --allow_cpu"
        )

    server = FastTTSServer(
        model_name=args.model,
        ckpt_file=args.ckpt_file,
        vocab_file=args.vocab_file,
        ref_dir=Path(args.ref_dir),
        device=device,
        default_nfe_step=args.nfe_step,
        cfg_strength=args.cfg_strength,
        sway_sampling_coef=args.sway_sampling_coef,
        speed=args.speed,
        max_ref_sec=args.max_ref_sec,
        use_compile=args.compile,
        temp_dir=Path(args.temp_dir),
        force_rebuild_ref=args.rebuild_ref,
    )
    try:
        serve(args.host, args.port, server)
    except KeyboardInterrupt:
        logger.info("收到中断，正在退出...")
    finally:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
