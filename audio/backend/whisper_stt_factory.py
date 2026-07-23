from __future__ import annotations

import os
from typing import Any

from whisper_stt_base import BaseWhisperSTT
from whisper_stt_faster import FasterWhisperSTT, faster_whisper_available
from whisper_stt_qwen3 import Qwen3AsrSTT, qwen3_asr_available
from whisper_stt_torch import TorchWhisperSTT, transformers_whisper_available


def resolve_whisper_backend(config: Any) -> str:
    backend = (os.getenv("VPET_WHISPER_BACKEND") or config.whisper_backend or "auto").strip().lower()
    if backend not in ("", "auto", "faster", "torch", "qwen3"):
        raise ValueError(f"不支持的 Whisper backend: {backend}")

    if backend in ("", "auto"):
        if qwen3_asr_available():
            return "qwen3"
        if transformers_whisper_available():
            return "torch"
        if faster_whisper_available():
            return "faster"
        # 保留原行为：默认仍走 faster，后续由加载阶段给出缺失依赖错误
        return "faster"

    return backend


def create_whisper_stt(config: Any) -> BaseWhisperSTT:
    backend = resolve_whisper_backend(config)
    if backend == "qwen3":
        return Qwen3AsrSTT(config)
    if backend == "torch":
        return TorchWhisperSTT(config)
    return FasterWhisperSTT(config)
