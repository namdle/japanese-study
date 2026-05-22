"""Integration tests for /api/voice/turn and /api/audio/{filename}.

We override both the LLM and Speech provider dependencies so no real
SDK calls are made.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_provider_for_user_dep
from app.api.voice import get_speech_provider_dep
from app.config import Settings
from app.db import reset_engine_for_tests
from app.deps import CurrentUser
from app.llm.base import ChatResponse, Message
from app.main import create_app
from app.speech.base import SpeechProvider, SynthesizedAudio, TutorVoice


class FakeLLM:
    name = "fake-llm"

    def __init__(self, reply: str = "そうですね!") -> None:
        self.reply = reply
        self.calls: list[tuple[list[Message], str]] = []

    def chat(self, messages, *, system, images=None, temperature=0.6):  # noqa: ARG002
        self.calls.append((list(messages), system))
        return ChatResponse(text=self.reply)


class FakeSpeech(SpeechProvider):
    name = "fake-speech"

    def __init__(self) -> None:
        self.transcript = "こんにちは"
        self.synth_calls: list[tuple[str, TutorVoice]] = []
        self.transcribe_calls: list[bytes] = []

    def transcribe(self, audio: bytes, *, language: str = "ja-JP") -> str:  # noqa: ARG002
        self.transcribe_calls.append(audio)
        return self.transcript

    def synthesize(
        self, text: str, *, voice: TutorVoice, language: str = "ja-JP"  # noqa: ARG002
    ) -> SynthesizedAudio:
        self.synth_calls.append((text, voice))
        return SynthesizedAudio(audio=b"FAKE_MP3_BYTES", mime_type="audio/mpeg")


@pytest.fixture
def voice_setup(
    settings: Settings,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeLLM, FakeSpeech, int]]:
    monkeypatch.setenv("APP_DATA_DIR", str(settings.data_dir))
    reset_engine_for_tests()
    app = create_app()
    fake_llm = FakeLLM()
    fake_speech = FakeSpeech()

    def _override_llm(user: CurrentUser):  # noqa: ARG001
        return fake_llm

    def _override_speech(user: CurrentUser):  # noqa: ARG001
        return fake_speech

    app.dependency_overrides[get_provider_for_user_dep] = _override_llm
    app.dependency_overrides[get_speech_provider_dep] = _override_speech

    with TestClient(app) as client:
        u = client.post("/api/users", json={"name": "Sora"}).json()
        yield client, fake_llm, fake_speech, u["id"]


def _post_turn(
    client: TestClient,
    user_id: int,
    audio_bytes: bytes,
    history: list[dict] | None = None,
):
    return client.post(
        "/api/voice/turn",
        files={"audio": ("rec.webm", io.BytesIO(audio_bytes), "audio/webm")},
        data={"history": json.dumps(history or [])},
        headers={"X-User-Id": str(user_id)},
    )


def test_voice_turn_runs_full_pipeline(voice_setup) -> None:
    client, llm, speech, user_id = voice_setup
    response = _post_turn(client, user_id, b"audio-bytes-here")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["transcript"] == "こんにちは"
    assert body["reply"] == "そうですね!"
    assert body["voice"] == "Misa"
    assert body["provider"] == "fake-llm"
    assert body["audio_url"].startswith("/api/audio/") and body["audio_url"].endswith(".mp3")

    # STT got the uploaded bytes
    assert speech.transcribe_calls == [b"audio-bytes-here"]
    # LLM got the transcript appended to history
    [(msgs, system)] = llm.calls
    assert msgs == [Message(role="user", content="こんにちは")]
    assert "Misa" in system
    # TTS got the reply text and Misa voice
    assert speech.synth_calls == [("そうですね!", TutorVoice.MISA)]


def test_voice_turn_includes_prior_history(voice_setup) -> None:
    client, llm, _, user_id = voice_setup
    history = [
        {"role": "user", "content": "やあ"},
        {"role": "assistant", "content": "やあ、元気?"},
    ]
    response = _post_turn(client, user_id, b"audio", history=history)
    assert response.status_code == 200, response.json()

    [(msgs, _)] = llm.calls
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1].content == "こんにちは"


def test_voice_turn_serves_generated_audio(voice_setup) -> None:
    client, _, _, user_id = voice_setup
    response = _post_turn(client, user_id, b"audio").json()

    audio_resp = client.get(response["audio_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"] == "audio/mpeg"
    assert audio_resp.content == b"FAKE_MP3_BYTES"


def test_voice_turn_400_when_audio_empty(voice_setup) -> None:
    client, _, _, user_id = voice_setup
    response = _post_turn(client, user_id, b"")
    assert response.status_code == 400


def test_voice_turn_400_when_transcript_empty(voice_setup) -> None:
    client, _, speech, user_id = voice_setup
    speech.transcript = "   "
    response = _post_turn(client, user_id, b"audio")
    assert response.status_code == 400
    assert "speech" in response.json()["detail"].lower()


def test_voice_turn_uses_hiro_when_user_voice_is_hiro(voice_setup) -> None:
    client, _, speech, user_id = voice_setup
    client.patch(
        f"/api/users/{user_id}",
        json={"voice": "Hiro"},
        headers={"X-User-Id": str(user_id)},
    )
    response = _post_turn(client, user_id, b"audio").json()
    assert response["voice"] == "Hiro"
    [(_, voice)] = speech.synth_calls
    assert voice == TutorVoice.HIRO


def test_audio_endpoint_404_for_missing_file(voice_setup) -> None:
    client, _, _, _ = voice_setup
    assert client.get("/api/audio/nonexistent.mp3").status_code == 404


def test_audio_endpoint_400_for_path_traversal(voice_setup) -> None:
    client, _, _, _ = voice_setup
    # Slash inside filename is rejected before path resolution.
    assert client.get("/api/audio/..%2Fjapanese.db").status_code in (400, 404)


def test_audio_endpoint_400_for_unsupported_extension(voice_setup) -> None:
    client, _, _, _ = voice_setup
    response = client.get("/api/audio/foo.exe")
    assert response.status_code == 400


def test_voice_turn_400_for_malformed_history(voice_setup) -> None:
    client, _, _, user_id = voice_setup
    response = client.post(
        "/api/voice/turn",
        files={"audio": ("rec.webm", io.BytesIO(b"audio"), "audio/webm")},
        data={"history": "not-json"},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 400
