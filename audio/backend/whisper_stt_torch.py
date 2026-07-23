from __future__ import annotations

import os
import tempfile
from typing import Any

from transcript_utils import collapse_repetitive_transcript
from whisper_stt_base import BaseWhisperSTT

_TRANSFORMERS_AVAILABLE = True
try:
    import torch
    from transformers import pipeline
except ImportError:
    _TRANSFORMERS_AVAILABLE = False
    torch = None
    pipeline = None


def transformers_whisper_available() -> bool:
    return _TRANSFORMERS_AVAILABLE


def torch_cuda_available() -> bool:
    try:
        return bool(torch is not None and torch.cuda.is_available())
    except Exception:
        return False


def torch_whisper_model_id(name: str) -> str:
    alias = {
        "tiny": "openai/whisper-tiny",
        "base": "openai/whisper-base",
        "small": "openai/whisper-small",
        "medium": "openai/whisper-medium",
        "large": "openai/whisper-large-v3-turbo",
        "large-v3": "openai/whisper-large-v3",
        "large-v3-turbo": "openai/whisper-large-v3-turbo",
    }
    return alias.get((name or "base").strip().lower(), name)


class TorchWhisperSTT(BaseWhisperSTT):
    def __init__(self, config: Any):
        super().__init__(config)
        self._model = None

    def _configure_hf_endpoint(self) -> None:
        mirror = (os.getenv("VPET_HF_ENDPOINT") or os.getenv("VPET_WHISPER_HF_MIRROR") or self.cfg.whisper_hf_mirror or "").strip()
        if mirror and not os.getenv("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = mirror

    def preload(self) -> None:
        self._load()

    def _load(self) -> None:
        if self._model is not None:
            return
        if not _TRANSFORMERS_AVAILABLE:
            raise ImportError("请安装 transformers 和 torch 以使用 torch Whisper 后端")

        device = (os.getenv("VPET_WHISPER_DEVICE") or self.cfg.whisper_device or "auto").strip().lower()
        compute = (os.getenv("VPET_WHISPER_COMPUTE") or self.cfg.whisper_compute or "auto").strip().lower()

        has_cuda = torch_cuda_available()
        if device in ("", "auto"):
            device = "cuda" if has_cuda else "cpu"
        if device.startswith("cuda") and not has_cuda:
            print("[WhisperSTT] torch CUDA 不可用，自动回退到 CPU")
            device = "cpu"

        if compute in ("", "auto", "int8"):
            compute = "float16" if device.startswith("cuda") else "float32"

        model_id = torch_whisper_model_id(self.cfg.whisper_model)
        self._configure_hf_endpoint()
        dtype = torch.float16 if compute == "float16" and device.startswith("cuda") else torch.float32
        pipe_device = 0 if device.startswith("cuda") else "cpu"

        print(f"[WhisperSTT] 加载 torch Whisper '{model_id}' ({device}/{compute})，由 HuggingFace 缓存管理 …")
        self._model = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            device=pipe_device,
            torch_dtype=dtype,
        )
        self.cfg.whisper_device = device
        self.cfg.whisper_compute = compute
        self.cfg.whisper_backend = "torch"

    def _decode(self, wav_path: str, language: str | None = None) -> str:
        kwargs: dict[str, Any] = {"task": "transcribe"}
        if language:
            kwargs["language"] = language

        result = self._model(
            wav_path,
            generate_kwargs=kwargs,
            return_timestamps=False,
        )

        text = (result.get("text") or "").strip()
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

            lang = self.resolve_language()
            text = self._decode(tmp.name, language=lang)
            if self.is_cancelled(cancel_event):
                return ""
            return collapse_repetitive_transcript(text)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
