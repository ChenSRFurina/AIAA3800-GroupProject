from __future__ import annotations

import importlib
import os
import ctypes
import tempfile
from typing import Any

from transcript_utils import collapse_repetitive_transcript
from whisper_stt_base import BaseWhisperSTT, get_models_dir

_FASTER_WHISPER_AVAILABLE = True
try:
    from faster_whisper import WhisperModel
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None


def faster_whisper_available() -> bool:
    return _FASTER_WHISPER_AVAILABLE


def _missing_windows_cuda_runtime_dlls() -> list[str]:
    if os.name != "nt":
        return []

    required = [
        "cublas64_12.dll",
        "cublasLt64_12.dll",
    ]
    missing: list[str] = []
    for dll_name in required:
        try:
            ctypes.WinDLL(dll_name)
        except OSError:
            missing.append(dll_name)
    return missing


def whisper_cuda_available() -> bool:
    if os.name == "nt":
        missing = _missing_windows_cuda_runtime_dlls()
        if missing:
            print(f"[WhisperSTT] 缺少 CUDA 运行时 DLL，改用 CPU: {', '.join(missing)}")
            return False

    try:
        ct2 = importlib.import_module("ctranslate2")
        return int(ct2.get_cuda_device_count()) > 0
    except Exception:
        pass

    try:
        torch_mod = importlib.import_module("torch")
        return bool(torch_mod.cuda.is_available())
    except Exception:
        return False


def is_whisper_cuda_runtime_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "cublas64_12.dll",
        "cublaslt64_12.dll",
        "cudnn",
        "cuda",
        "cannot be loaded",
        "not found",
        "failed to create cublas handle",
    )
    return any(marker in text for marker in markers)


class FasterWhisperSTT(BaseWhisperSTT):
    _MODEL_FILES = [
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    ]
    _MODEL_REPO = "Systran/faster-whisper-base"
    _MIRROR_BASE = "https://hf-mirror.com"

    def __init__(self, config: Any):
        super().__init__(config)
        self._model = None

    def _build_model(self, model_path: str, device: str, compute: str) -> Any:
        return WhisperModel(model_path, device=device, compute_type=compute)

    def preload(self) -> None:
        self._load()

    def _download_model(self) -> str:
        import httpx

        cache_dir = self.cfg.model_cache_dir or get_models_dir()
        model_dir = os.path.join(cache_dir, "faster-whisper-base")
        os.makedirs(model_dir, exist_ok=True)

        all_exist = all(os.path.exists(os.path.join(model_dir, f)) for f in self._MODEL_FILES)
        if all_exist:
            print(f"[WhisperSTT] 模型已存在: {model_dir}")
            return model_dir

        print(f"[WhisperSTT] 下载模型文件到 {model_dir} …")
        for filename in self._MODEL_FILES:
            url = f"{self._MIRROR_BASE}/{self._MODEL_REPO}/resolve/main/{filename}"
            dest = os.path.join(model_dir, filename)

            if os.path.exists(dest):
                size = os.path.getsize(dest)
                if filename == "model.bin" and size > 100_000_000:
                    print(f"  [跳过] {filename} ({size / 1024 / 1024:.0f} MB)")
                    continue
                if filename != "model.bin":
                    print(f"  [跳过] {filename}")
                    continue

            print(f"  [下载] {filename} …")
            with httpx.stream("GET", url, follow_redirects=True, timeout=600) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(dest, "wb") as file_obj:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        file_obj.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and downloaded % (total // 20 + 1) < 8192:
                            pct = downloaded * 100 // total
                            print(f"    {pct}% ({downloaded / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} MB)", end="\r")
                print(f"    100% ({downloaded / 1024 / 1024:.0f} MB) ✓")

        print(f"[WhisperSTT] 模型下载完成: {model_dir}")
        return model_dir

    def _fallback_to_cpu(self, model_path: str, reason: Exception) -> None:
        print(f"[WhisperSTT] CUDA 不可用，回退 CPU: {reason}")
        self.cfg.whisper_device = "cpu"
        self.cfg.whisper_compute = "int8"
        self.cfg.whisper_backend = "faster"
        self._model = self._build_model(model_path, "cpu", "int8")

    def _load(self) -> None:
        if self._model is not None:
            return
        if not _FASTER_WHISPER_AVAILABLE:
            raise ImportError("请安装 faster-whisper: pip install faster-whisper")

        device = (os.getenv("VPET_WHISPER_DEVICE") or self.cfg.whisper_device or "auto").strip().lower()
        compute = (os.getenv("VPET_WHISPER_COMPUTE") or self.cfg.whisper_compute or "auto").strip().lower()

        has_cuda = whisper_cuda_available()
        if device in ("", "auto"):
            device = "cuda" if has_cuda else "cpu"
        if device.startswith("cuda") and not has_cuda:
            print("[WhisperSTT] 检测到未启用 CUDA，自动回退到 CPU")
            device = "cpu"

        if compute in ("", "auto"):
            compute = "float16" if device.startswith("cuda") else "int8"

        model_path = self._download_model()
        print(f"[WhisperSTT] 加载 faster-whisper '{self.cfg.whisper_model}' ({device}/{compute}) …")
        try:
            self._model = self._build_model(model_path, device, compute)
        except Exception as exc:
            if device.startswith("cuda"):
                self._fallback_to_cpu(model_path, exc)
                device = "cpu"
                compute = "int8"
            else:
                raise

        self.cfg.whisper_device = device
        self.cfg.whisper_compute = compute
        self.cfg.whisper_backend = "faster"

    def _decode(self, wav_path: str, language: str | None = None) -> str:
        kwargs: dict[str, Any] = {
            "vad_filter": True,
            "beam_size": 5,
            "condition_on_previous_text": False,
        }
        if language:
            kwargs["language"] = language

        segments, _info = self._model.transcribe(wav_path, **kwargs)
        seg_list = list(segments)
        text = " ".join(seg.text.strip() for seg in seg_list).strip()
        return text

    def transcribe(self, wav_bytes: bytes, cancel_event: Any | None = None) -> str:
        if self.is_cancelled(cancel_event):
            return ""
        self._load()
        if self.is_cancelled(cancel_event):
            return ""

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(wav_bytes)
            tmp.close()
            if self.is_cancelled(cancel_event):
                return ""

            try:
                lang = self.resolve_language()
                text = self._decode(tmp.name, language=lang)
                if self.is_cancelled(cancel_event):
                    return ""
                return collapse_repetitive_transcript(text)
            except Exception as exc:
                if self.cfg.whisper_device.startswith("cuda") and is_whisper_cuda_runtime_error(exc):
                    model_path = self._download_model()
                    self._fallback_to_cpu(model_path, exc)
                    lang = self.resolve_language()
                    text = self._decode(tmp.name, language=lang)
                    if self.is_cancelled(cancel_event):
                        return ""
                    return collapse_repetitive_transcript(text)
                raise
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
