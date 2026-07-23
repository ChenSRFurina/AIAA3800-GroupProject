from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseWhisperSTT(ABC):
    """Whisper STT backend base class."""

    def __init__(self, config: Any):
        self.cfg = config

    @abstractmethod
    def transcribe(self, wav_bytes: bytes, cancel_event: Any | None = None) -> str:
        """Transcribe WAV bytes into text."""

    def preload(self) -> None:
        """Eagerly load model resources if the backend supports it."""
        return

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

    def resolve_language(self) -> str | None:
        """Resolve Whisper language selection.

        force mode uses a fixed language; whitelist mode uses auto-detect for mixed zh/en
        unless only a single allowed language remains.
        """
        allowed = self.resolve_allowed_languages()
        mode = self.resolve_language_mode()
        if mode == "force":
            return self.resolve_force_language(allowed)
        if len(allowed) == 1:
            return allowed[0]
        return None


def get_models_dir() -> str:
    """Get project-local models directory."""
    audio_file = Path(__file__).resolve()
    project_models = audio_file.parent.parent / "models"
    project_models.mkdir(parents=True, exist_ok=True)
    return str(project_models)
