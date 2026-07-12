"""OpenAI speech adapter: Whisper (STT) + TTS.

Uses the openai SDK. Reads OPENAI_API_KEY from the environment.

Voice mapping for Japanese:
- Misa  -> 'shimmer' (warm female-leaning voice)
- Hiro  -> 'echo' (clear male-leaning voice)
"""

from __future__ import annotations

import io
import os

import openai

from app.speech.base import (
    SpeechProvider,
    SpeechProviderUnavailableError,
    SynthesizedAudio,
    TutorVoice,
)

VOICE_MAP: dict[TutorVoice, str] = {
    TutorVoice.MISA: "shimmer",
    TutorVoice.HIRO: "echo",
}


class OpenAISpeechProvider(SpeechProvider):
    name = "openai"

    def __init__(self, *, api_key: str | None = None, client=None) -> None:
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.getenv("OPENAI_API_KEY")
            if not resolved_key:
                raise SpeechProviderUnavailableError(
                    "OpenAI speech provider is not configured. Set OPENAI_API_KEY."
                )
            self._client = openai.OpenAI(api_key=resolved_key)

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str = "ja-JP",
        phrase_hints: list[str] | None = None,
        strong_hints: list[str] | None = None,
    ) -> str:
        # Whisper expects a file-like object with a name hint for format detection.
        audio_file = io.BytesIO(audio)
        audio_file.name = "recording.webm"
        # language param for Whisper is ISO 639-1 (e.g., "ja").
        lang_code = language.split("-")[0] if "-" in language else language
        # Whisper biases toward words that appear in `prompt`; feed it the
        # learner name + expected vocabulary so near-homophones (ナム vs 眠い)
        # resolve correctly. (Whisper has no per-term boost, so both go in.)
        kwargs: dict[str, object] = {}
        combined = (strong_hints or []) + (phrase_hints or [])
        deduped = list(dict.fromkeys(h for h in combined if h and h.strip()))
        if deduped:
            kwargs["prompt"] = "、".join(deduped)
        response = self._client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=lang_code,
            **kwargs,
        )
        return (response.text or "").strip()

    def synthesize(
        self,
        text: str,
        *,
        voice: TutorVoice,
        language: str = "ja-JP",  # noqa: ARG002 - OpenAI TTS auto-detects language
    ) -> SynthesizedAudio:
        oai_voice = VOICE_MAP[voice]
        response = self._client.audio.speech.create(
            model="tts-1",
            voice=oai_voice,
            input=text,
            response_format="mp3",
        )
        audio_bytes = response.content
        return SynthesizedAudio(audio=audio_bytes, mime_type="audio/mpeg")
