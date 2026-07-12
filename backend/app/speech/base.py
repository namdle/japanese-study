"""Common speech provider types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TutorVoice(str, Enum):
    MISA = "Misa"
    HIRO = "Hiro"

    @classmethod
    def from_string(cls, value: str) -> TutorVoice:
        try:
            return cls(value)
        except ValueError:
            return cls.MISA  # safe default


@dataclass(frozen=True)
class SynthesizedAudio:
    """Bytes of synthesized speech plus its mime type."""

    audio: bytes
    mime_type: str  # e.g. "audio/mpeg" for MP3


class SpeechProviderUnavailableError(RuntimeError):
    """Raised when the speech provider isn't configured (missing creds, etc)."""


class SpeechProvider(Protocol):
    """Provider adapter contract."""

    name: str

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str = "ja-JP",
        phrase_hints: list[str] | None = None,
    ) -> str: ...

    def synthesize(
        self,
        text: str,
        *,
        voice: TutorVoice,
        language: str = "ja-JP",
    ) -> SynthesizedAudio: ...
