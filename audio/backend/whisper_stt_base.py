from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


KNOWN_PROMPT_LEAKS = [
    "请将语音准确转写为简体中文文本",
    "语音准确转写为简体中文文本",
    "准确转写为简体中文文本",
    "transcribe speech accurately in english text",
    "speech accurately in english text",
]


class BaseWhisperSTT(ABC):
    """Whisper STT backend base class."""

    def __init__(self, config: Any):
        self.cfg = config

    @abstractmethod
    def transcribe(self, wav_bytes: bytes, cancel_event: Any | None = None) -> str:
        """Transcribe WAV bytes into text."""

    @staticmethod
    def is_cancelled(cancel_event: Any | None) -> bool:
        return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())

    def resolve_allowed_languages(self) -> list[str]:
        raw = (os.getenv("VPET_WHISPER_ALLOWED_LANGS") or self.cfg.whisper_allowed_languages or "zh,en").strip().lower()
        items = [x.strip() for x in raw.split(",") if x.strip()]
        normalized: list[str] = []
        for item in items:
            if item in ("zh", "en") and item not in normalized:
                normalized.append(item)
        return normalized or ["zh", "en"]

    def resolve_language_mode(self) -> str:
        mode = (os.getenv("VPET_WHISPER_LANGUAGE_MODE") or self.cfg.whisper_language_mode or "whitelist").strip().lower()
        return "force" if mode == "force" else "whitelist"

    def resolve_force_language(self, allowed: list[str]) -> str:
        forced = (os.getenv("VPET_WHISPER_FORCE_LANGUAGE") or self.cfg.whisper_force_language or "").strip().lower()
        if forced in ("zh", "en"):
            return forced
        return allowed[0] if allowed else "zh"

    def use_initial_prompt(self) -> bool:
        raw = os.getenv("VPET_WHISPER_USE_INITIAL_PROMPT")
        if raw is not None:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(self.cfg.whisper_use_initial_prompt)

    def resolve_initial_prompt(self) -> str:
        return (os.getenv("VPET_WHISPER_INITIAL_PROMPT") or self.cfg.whisper_initial_prompt or "").strip()

    @staticmethod
    def normalize_compare_text(text: str) -> str:
        if not text:
            return ""
        normalized = re.sub(r"\s+", "", text).strip().lower()
        return re.sub(r"[，。,.!?！？:：;；\-_'\"“”‘’()（）]", "", normalized)

    def looks_like_prompt_leak(self, text: str) -> bool:
        candidate = self.normalize_compare_text(text)
        if not candidate:
            return True

        prompt = self.normalize_compare_text(self.resolve_initial_prompt())
        if prompt and (candidate == prompt or candidate in prompt or prompt in candidate):
            return True

        for leak in KNOWN_PROMPT_LEAKS:
            if candidate == self.normalize_compare_text(leak):
                return True

        return False


def get_models_dir() -> str:
    """Get project-local models directory."""
    audio_file = Path(__file__).resolve()
    project_models = audio_file.parent.parent / "models"
    project_models.mkdir(parents=True, exist_ok=True)
    return str(project_models)
