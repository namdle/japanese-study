"""Unit tests for OpenAISpeechProvider with mocked client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.speech.base import SpeechProviderUnavailableError, TutorVoice
from app.speech.openai_speech import OpenAISpeechProvider


def test_transcribe_calls_whisper() -> None:
    client = MagicMock()
    client.audio.transcriptions.create.return_value = SimpleNamespace(text="こんにちは")
    provider = OpenAISpeechProvider(client=client)

    result = provider.transcribe(b"audio-bytes", language="ja-JP")

    assert result == "こんにちは"
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "whisper-1"
    assert kwargs["language"] == "ja"


def test_synthesize_misa_uses_shimmer() -> None:
    client = MagicMock()
    client.audio.speech.create.return_value = SimpleNamespace(content=b"mp3data")
    provider = OpenAISpeechProvider(client=client)

    audio = provider.synthesize("ありがとう", voice=TutorVoice.MISA)

    assert audio.audio == b"mp3data"
    assert audio.mime_type == "audio/mpeg"
    kwargs = client.audio.speech.create.call_args.kwargs
    assert kwargs["voice"] == "shimmer"
    assert kwargs["model"] == "tts-1"
    assert kwargs["input"] == "ありがとう"


def test_synthesize_hiro_uses_echo() -> None:
    client = MagicMock()
    client.audio.speech.create.return_value = SimpleNamespace(content=b"mp3data")
    provider = OpenAISpeechProvider(client=client)

    provider.synthesize("hi", voice=TutorVoice.HIRO)

    kwargs = client.audio.speech.create.call_args.kwargs
    assert kwargs["voice"] == "echo"


def test_raises_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SpeechProviderUnavailableError):
        OpenAISpeechProvider()
