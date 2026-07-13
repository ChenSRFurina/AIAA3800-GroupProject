"""
F5-TTS 常驻服务端：加载模型 + 学习 ref/ 参考音色，保持 GPU 常驻。

窗口 1（不要关）:
    python start_server.py

可选参数:
    python start_server.py --host 127.0.0.1 --port 8765 --nfe_step 8
"""

from __future__ import annotations

import argparse
import gc
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
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from f5_tts.infer.utils_infer import (  # noqa: E402
    infer_batch_process,
    load_model,
    load_vocoder,
    preprocess_ref_audio_text,
)
from f5_tts.model.utils import seed_everything  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("f5_fast_server")


def resolve_device(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


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
    ):
        self.device = device
        self.default_nfe_step = default_nfe_step
        self.cfg_strength = cfg_strength
        self.sway_sampling_coef = sway_sampling_coef
        self.speed = speed
        self.ref_dir = ref_dir
        self.lock = threading.Lock()

        if self.device.startswith("cuda"):
            torch.backends.cudnn.benchmark = True
            torch.set_float32_matmul_precision("high")

        model_cfg = OmegaConf.load(str(SRC / "f5_tts" / "configs" / f"{model_name}.yaml"))
        model_cls = get_class(f"f5_tts.model.{model_cfg.model.backbone}")
        model_arc = model_cfg.model.arch
        self.mel_spec_type = model_cfg.model.mel_spec.mel_spec_type
        self.sample_rate = model_cfg.model.mel_spec.target_sample_rate

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

        self.ref_audio_path: str | None = None
        self.ref_text: str = ""
        self.ref_audio_tensor: torch.Tensor | None = None
        self.ref_sr: int = self.sample_rate

        self.load_reference()
        self.warmup()

    def load_reference(self) -> None:
        wav_path, ref_text = find_ref_pair(self.ref_dir)
        logger.info("学习参考音色: %s", wav_path.name)
        processed_audio, processed_text = preprocess_ref_audio_text(str(wav_path), ref_text)
        audio, sr = torchaudio.load(processed_audio)

        self.ref_audio_path = processed_audio
        self.ref_text = processed_text
        self.ref_audio_tensor = audio
        self.ref_sr = sr
        logger.info("参考文本: %s", self.ref_text.strip())
        logger.info("参考音频时长: %.2fs", audio.shape[-1] / sr)

    def warmup(self, text: str = "你好，这是一次预热。") -> None:
        logger.info("GPU 预热中...")
        t0 = time.perf_counter()
        self.synthesize(text, nfe_step=self.default_nfe_step, seed=0)
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        logger.info("预热完成，耗时 %.3fs", time.perf_counter() - t0)

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

        assert self.ref_audio_tensor is not None

        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        t0 = time.perf_counter()

        with self.lock:
            wave, sr, _ = next(
                infer_batch_process(
                    (self.ref_audio_tensor, self.ref_sr),
                    self.ref_text,
                    [text],
                    self.model,
                    self.vocoder,
                    mel_spec_type=self.mel_spec_type,
                    progress=None,
                    nfe_step=nfe,
                    cfg_strength=cfg,
                    sway_sampling_coef=self.sway_sampling_coef,
                    speed=spd,
                    device=self.device,
                    streaming=False,
                )
            )

        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        infer_s = time.perf_counter() - t0

        if wave is None:
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
                            "sample_rate": server.sample_rate,
                            "ref_text": server.ref_text.strip(),
                            "default_nfe_step": server.default_nfe_step,
                        },
                    )
                    continue

                if cmd in ("reload_ref", "reload"):
                    server.load_reference()
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
                    "合成完成 | %.1f ms | nfe=%s | text=%s",
                    infer_s * 1000.0,
                    req.get("nfe_step") or server.default_nfe_step,
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
    p.add_argument("--ckpt_file", default="", help="本地权重路径；为空则从 HuggingFace 缓存下载")
    p.add_argument("--vocab_file", default="")
    p.add_argument("--ref_dir", default=str(HERE / "ref"), help="参考音色目录（含 wav + txt）")
    p.add_argument("--device", default=None)
    p.add_argument("--nfe_step", type=int, default=8, help="默认 ODE 步数，越小越快（建议 4~16）")
    p.add_argument("--cfg_strength", type=float, default=2.0)
    p.add_argument("--sway_sampling_coef", type=float, default=-1.0)
    p.add_argument("--speed", type=float, default=1.0)
    return p.parse_args()


def main():
    args = parse_args()
    device = resolve_device(args.device)
    if not device.startswith("cuda"):
        logger.warning("当前设备为 %s，很难达到 0.3s；请确认已安装 CUDA 版 PyTorch", device)

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
