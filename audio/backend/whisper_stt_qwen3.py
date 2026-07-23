from __future__ import annotations

import os
import tempfile
from typing import Any

from transcript_utils import collapse_repetitive_transcript
from whisper_stt_base import BaseWhisperSTT

_QWEN3_ASR_AVAILABLE = True
try:
    import torch
    from qwen_asr import Qwen3ASRModel
except ImportError:
    _QWEN3_ASR_AVAILABLE = False
    torch = None
    Qwen3ASRModel = None


def qwen3_asr_available() -> bool:
    return _QWEN3_ASR_AVAILABLE


def qwen3_torch_cuda_available() -> bool:
    try:
        return bool(torch is not None and torch.cuda.is_available())
    except Exception:
        return False


class Qwen3AsrSTT(BaseWhisperSTT):
    """Qwen3-ASR backend via official qwen_asr SDK."""

    def __init__(self, config: Any):
        super().__init__(config)
        self._model = None

    def _configure_hf_endpoint(self) -> None:
        mirror = (
            os.getenv("VPET_HF_ENDPOINT")
            or os.getenv("VPET_WHISPER_HF_MIRROR")
            or self.cfg.whisper_hf_mirror
            or ""
        ).strip()
        if mirror and not os.getenv("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = mirror

    def preload(self) -> None:
        self._load()

    def _load(self) -> None:
        if self._model is not None:
            return
        if not _QWEN3_ASR_AVAILABLE:
            raise ImportError("请安装 qwen-asr、torch 以使用 Qwen3-ASR 后端")

        model_id = (
            os.getenv("VPET_QWEN3_ASR_MODEL")
            or getattr(self.cfg, "qwen3_asr_model", "")
            or "Qwen/Qwen3-ASR-0.6B"
        ).strip()
        forced_aligner = (
            os.getenv("VPET_QWEN3_FORCED_ALIGNER")
            or getattr(self.cfg, "qwen3_forced_aligner", "")
            or "Qwen/Qwen3-ForcedAligner-0.6B"
        ).strip()
        max_batch_size = int(
            os.getenv("VPET_QWEN3_MAX_BATCH_SIZE")
            or getattr(self.cfg, "qwen3_max_inference_batch_size", 32)
            or 32
        )
        max_new_tokens = int(
            os.getenv("VPET_QWEN3_MAX_NEW_TOKENS")
            or getattr(self.cfg, "qwen3_max_new_tokens", 256)
            or 256
        )

        device = (os.getenv("VPET_WHISPER_DEVICE") or self.cfg.whisper_device or "auto").strip().lower()
        compute = (os.getenv("VPET_WHISPER_COMPUTE") or self.cfg.whisper_compute or "auto").strip().lower()

        has_cuda = qwen3_torch_cuda_available()
        if device in ("", "auto"):
            device = "cuda" if has_cuda else "cpu"
        if device.startswith("cuda") and not has_cuda:
            print("[Qwen3ASR] torch CUDA 不可用，自动回退到 CPU")
            device = "cpu"

        if compute in ("", "auto", "int8", "float16"):
            compute = "bfloat16" if device.startswith("cuda") else "float32"

        self._configure_hf_endpoint()
        if compute == "bfloat16" and device.startswith("cuda"):
            dtype = torch.bfloat16
        elif compute == "float16" and device.startswith("cuda"):
            dtype = torch.float16
        else:
            dtype = torch.float32

        device_map = os.getenv("VPET_QWEN3_DEVICE_MAP", "").strip()
        if not device_map:
            device_map = "cuda:0" if device.startswith("cuda") else "cpu"

        print(f"[Qwen3ASR] 加载模型 '{model_id}' ({device}/{compute}) …")
        self._model = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=device_map,
            max_inference_batch_size=max_batch_size,
            max_new_tokens=max_new_tokens,
            forced_aligner=forced_aligner,
            forced_aligner_kwargs={
                "dtype": dtype,
                "device_map": device_map,
            },
        )

        self.cfg.whisper_device = device
        self.cfg.whisper_compute = compute
        self.cfg.whisper_backend = "qwen3"

    def _decode(self, wav_path: str, language: str | None = None) -> str:
        lang_name = None
        if language == "zh":
            lang_name = "Chinese"
        elif language == "en":
            lang_name = "English"

        results = self._model.transcribe(
            audio=[wav_path],
            language=[lang_name] if lang_name else None,
            return_time_stamps=False,
        )

        if not results:
            return ""

        first = results[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text.strip()
        if isinstance(first, dict):
            return (first.get("text") or "").strip()
        if isinstance(first, str):
            return first.strip()
        return ""

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
