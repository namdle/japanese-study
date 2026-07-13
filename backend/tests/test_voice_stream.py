"""Tests for the streaming voice turn: SentenceStreamer + the SSE endpoint.

The SSE endpoint is exercised end-to-end with fakes; we parse the emitted
events from the response body and assert on ordering, content, and the
persisted assistant turn.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_provider_for_user_dep
from app.api.sessions import _FALLBACK_REPLY
from app.api.voice import get_speech_provider_dep
from app.config import Settings
from app.db import reset_engine_for_tests
from app.deps import CurrentUser
from app.llm.base import ChatResponse
from app.main import create_app
from app.session.streaming import SentenceStreamer
from app.speech.base import SpeechProvider, SynthesizedAudio, TutorVoice

# --------------------------------------------------------------------------- #
# SentenceStreamer
# --------------------------------------------------------------------------- #


def test_streamer_splits_on_japanese_enders() -> None:
    s = SentenceStreamer()
    out = s.feed("こんにちは。元気ですか?すご")
    assert out == ["こんにちは。", "元気ですか?"]
    out = s.feed("いですね!")
    assert out == ["すごいですね!"]
    assert s.flush() is None
    assert s.full_text == "こんにちは。元気ですか?すごいですね!"


def test_streamer_flush_returns_trailing_fragment() -> None:
    s = SentenceStreamer()
    assert s.feed("はい。それは") == ["はい。"]
    assert s.flush() == "それは"


def test_streamer_withholds_marker_lines_from_speech() -> None:
    s = SentenceStreamer()
    sentences: list[str] = []
    for delta in ["こんにちは。", "\n[HIRA", "GANA] こんにちは。\n[EN] Hello."]:
        sentences.extend(s.feed(delta))
    sentences_rest = s.flush()
    assert sentences == ["こんにちは。"]
    assert sentences_rest is None
    # Full text keeps everything for parse_tutor_reply.
    assert "[HIRAGANA]" in s.full_text
    assert "[EN] Hello." in s.full_text


def test_streamer_emits_pre_marker_fragment_without_ender() -> None:
    s = SentenceStreamer()
    out = s.feed("じゃあね[EN] Bye")
    assert out == ["じゃあね"]
    assert s.flush() is None


def test_streamer_newline_is_a_boundary() -> None:
    s = SentenceStreamer()
    assert s.feed("一行目\n二行目") == ["一行目"]
    assert s.flush() == "二行目"


# --------------------------------------------------------------------------- #
# SSE endpoint
# --------------------------------------------------------------------------- #


class FakeStreamLLM:
    """LLM fake with a stream_chat capability."""

    name = "fake-stream-llm"

    def __init__(self) -> None:
        self.stream_deltas: list[str] = ["そうですね。", "いいですよ!"]
        self.next_replies: list[str] = []
        self.chat_calls: list[str] = []
        self.stream_calls = 0

    def chat(self, messages, *, system, images=None, temperature=0.6, max_tokens=None):  # noqa: ARG002
        self.chat_calls.append(system)
        text = self.next_replies.pop(0) if self.next_replies else "そうですね!"
        return ChatResponse(text=text)

    def stream_chat(self, messages, *, system, images=None, max_tokens=None):  # noqa: ARG002
        self.stream_calls += 1
        yield from self.stream_deltas


class FakeChatOnlyLLM:
    """LLM fake WITHOUT stream_chat — exercises the fallback path."""

    name = "fake-chat-llm"

    def __init__(self) -> None:
        self.next_replies: list[str] = []

    def chat(self, messages, *, system, images=None, temperature=0.6, max_tokens=None):  # noqa: ARG002
        text = self.next_replies.pop(0) if self.next_replies else "そうですね。はい!"
        return ChatResponse(text=text)


class FakeSpeech(SpeechProvider):
    name = "fake-speech"

    def __init__(self) -> None:
        self.transcript = "こんにちは"
        self.synthesized: list[str] = []

    def transcribe(  # noqa: ARG002
        self,
        audio: bytes,
        *,
        language: str = "ja-JP",
        phrase_hints: list[str] | None = None,
        strong_hints: list[str] | None = None,
    ) -> str:
        return self.transcript

    def synthesize(self, text: str, *, voice: TutorVoice, language: str = "ja-JP"):  # noqa: ARG002
        self.synthesized.append(text)
        return SynthesizedAudio(audio=f"MP3:{text}".encode(), mime_type="audio/mpeg")


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        assert event is not None and data is not None, block
        events.append((event, data))
    return events


@pytest.fixture
def stream_setup(
    settings: Settings,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeStreamLLM, FakeSpeech, int]]:
    monkeypatch.setenv("APP_DATA_DIR", str(settings.data_dir))
    reset_engine_for_tests()
    app = create_app()
    fake_llm = FakeStreamLLM()
    fake_speech = FakeSpeech()

    def _llm(user: CurrentUser):  # noqa: ARG001
        return fake_llm

    def _speech(user: CurrentUser):  # noqa: ARG001
        return fake_speech

    app.dependency_overrides[get_provider_for_user_dep] = _llm
    app.dependency_overrides[get_speech_provider_dep] = _speech

    with TestClient(app) as client:
        admin = client.post("/api/users", json={"name": "Mom"}).json()
        client.patch(f"/api/users/{admin['id']}", json={"is_admin": True})
        learner = client.post("/api/users", json={"name": "Sora"}).json()

        # Approve the first lesson so a session can start.
        topics = client.get("/api/curriculum/topics").json()
        lesson_id = topics[0]["lessons"][0]["id"]
        headers = {"X-User-Id": str(admin["id"])}
        client.put(
            f"/api/curriculum/lessons/{lesson_id}/plan",
            json={"body_markdown": "Greet warmly."},
            headers=headers,
        )
        client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=headers)

        yield client, fake_llm, fake_speech, int(learner["id"])


def _start_session(client: TestClient, learner_id: int) -> int:
    res = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    )
    assert res.status_code == 201, res.text
    return int(res.json()["session"]["id"])


def _post_stream(client: TestClient, session_id: int, learner_id: int):
    return client.post(
        f"/api/sessions/{session_id}/turn-audio/stream",
        headers={"X-User-Id": str(learner_id)},
        files={"audio": ("t.webm", io.BytesIO(b"fake-audio"), "audio/webm")},
    )


def test_stream_turn_emits_events_and_persists(stream_setup) -> None:
    client, llm, speech, learner_id = stream_setup
    session_id = _start_session(client, learner_id)
    speech.synthesized.clear()

    llm.stream_deltas = ["そうです", "ね。いいですよ!", "\n[HIRAGANA] そうですね。いいですよ!"]
    res = _post_stream(client, session_id, learner_id)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(res.text)
    names = [e for e, _ in events]
    assert names[0] == "transcript"
    assert events[0][1]["text"] == "こんにちは"
    assert names[-1] == "done"
    assert "aids" in names

    # Two sentences → two text events and two audio events, in order.
    texts = [d["delta"] for e, d in events if e == "text"]
    assert texts == ["そうですね。", "いいですよ!"]
    audios = [d for e, d in events if e == "audio"]
    assert len(audios) == 2
    assert base64.b64decode(audios[0]["b64"]) == "MP3:そうですね。".encode()

    # Markers never reach TTS.
    assert all("[HIRAGANA]" not in t for t in speech.synthesized)

    # Persisted turn: full Japanese text, combined audio, hiragana aid.
    done = events[-1][1]
    last_turn = done["turns"][-1]
    assert last_turn["role"] == "assistant"
    assert last_turn["text"] == "そうですね。いいですよ!"
    assert last_turn["hiragana"] == "そうですね。いいですよ!"
    assert last_turn["audio_url"]

    # The user's transcript was persisted too.
    assert done["turns"][-2]["role"] == "user"
    assert done["turns"][-2]["text"] == "こんにちは"

    # Replay works: the combined audio file is downloadable.
    audio_res = client.get(last_turn["audio_url"], headers={"X-User-Id": str(learner_id)})
    assert audio_res.status_code == 200
    assert audio_res.content == "MP3:そうですね。".encode() + "MP3:いいですよ!".encode()


def test_stream_turn_backfills_missing_aids_after_audio(stream_setup) -> None:
    """User wants hiragana but the tutor omitted it → one backfill chat AFTER
    the audio events (off the critical path)."""
    client, llm, speech, learner_id = stream_setup
    client.patch(
        f"/api/users/{learner_id}",
        json={"show_hiragana": True},
        headers={"X-User-Id": str(learner_id)},
    )
    session_id = _start_session(client, learner_id)
    llm.chat_calls.clear()

    llm.stream_deltas = ["こんにちは。"]  # no [HIRAGANA] line
    llm.next_replies = ["[HIRAGANA] こんにちは。"]  # backfill answer
    res = _post_stream(client, session_id, learner_id)
    events = parse_sse(res.text)
    names = [e for e, _ in events]

    # Exactly one non-streaming chat call (the backfill), and the audio event
    # came before the aids event.
    assert len(llm.chat_calls) == 1
    assert names.index("audio") < names.index("aids")
    aids = next(d for e, d in events if e == "aids")
    assert aids["hiragana"] == "こんにちは。"
    assert events[-1][1]["turns"][-1]["hiragana"] == "こんにちは。"


def test_stream_turn_falls_back_when_stream_is_empty(stream_setup) -> None:
    client, llm, speech, learner_id = stream_setup
    session_id = _start_session(client, learner_id)

    llm.stream_deltas = []  # empty stream
    llm.next_replies = ["", ""]  # non-streaming retry also empty → fallback text
    res = _post_stream(client, session_id, learner_id)
    events = parse_sse(res.text)

    done = events[-1]
    assert done[0] == "done"
    assert done[1]["turns"][-1]["text"] == _FALLBACK_REPLY


def test_stream_turn_works_without_stream_chat(stream_setup) -> None:
    """Providers without stream_chat still get chunked TTS."""
    client, _llm, speech, learner_id = stream_setup
    session_id = _start_session(client, learner_id)

    chat_only = FakeChatOnlyLLM()
    chat_only.next_replies = ["はい。わかりました!"]

    def _chat_only(user: CurrentUser):  # noqa: ARG001
        return chat_only

    client.app.dependency_overrides[get_provider_for_user_dep] = _chat_only

    res = _post_stream(client, session_id, learner_id)
    events = parse_sse(res.text)
    texts = [d["delta"] for e, d in events if e == "text"]
    assert texts == ["はい。", "わかりました!"]
    assert events[-1][0] == "done"


def test_stream_turn_rejects_empty_audio(stream_setup) -> None:
    client, _llm, _speech, learner_id = stream_setup
    session_id = _start_session(client, learner_id)
    res = client.post(
        f"/api/sessions/{session_id}/turn-audio/stream",
        headers={"X-User-Id": str(learner_id)},
        files={"audio": ("t.webm", io.BytesIO(b""), "audio/webm")},
    )
    assert res.status_code == 400
