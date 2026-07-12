"""Integration tests for /api/sessions/* endpoints.

We override the LLM and Speech provider dependencies with simple fakes so
no real SDK calls are made.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_provider_for_user_dep
from app.api.sessions import _FALLBACK_REPLY, _ensure_reading_aids, _tutor_reply
from app.api.voice import get_speech_provider_dep
from app.config import Settings
from app.db import get_engine, reset_engine_for_tests, topic_interests_table
from app.deps import CurrentUser
from app.llm.base import ChatResponse, Message, ParsedReply
from app.main import create_app
from app.speech.base import SpeechProvider, SynthesizedAudio, TutorVoice


class FakeLLM:
    name = "fake-llm"

    def __init__(self) -> None:
        self.next_replies: list[str] = []
        self.calls: list[tuple[list[Message], str]] = []

    def chat(self, messages, *, system, images=None, temperature=0.6, max_tokens=None):  # noqa: ARG002
        self.calls.append((list(messages), system))
        if self.next_replies:
            text = self.next_replies.pop(0)
        else:
            text = "そうですね!"
        return ChatResponse(text=text)


class FakeSpeech(SpeechProvider):
    name = "fake-speech"

    def __init__(self) -> None:
        self.transcript = "こんにちは"
        self.last_phrase_hints: list[str] | None = None
        self.last_strong_hints: list[str] | None = None

    def transcribe(  # noqa: ARG002
        self,
        audio: bytes,
        *,
        language: str = "ja-JP",
        phrase_hints: list[str] | None = None,
        strong_hints: list[str] | None = None,
    ) -> str:
        self.last_phrase_hints = phrase_hints
        self.last_strong_hints = strong_hints
        return self.transcript

    def synthesize(self, text: str, *, voice: TutorVoice, language: str = "ja-JP"):  # noqa: ARG002
        return SynthesizedAudio(audio=b"FAKE_MP3", mime_type="audio/mpeg")


@pytest.fixture
def session_setup(
    settings: Settings,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeLLM, FakeSpeech, int, int]]:
    """Build app, register fakes, seed an admin and a learner, return ids + helpers."""
    monkeypatch.setenv("APP_DATA_DIR", str(settings.data_dir))
    reset_engine_for_tests()
    app = create_app()
    fake_llm = FakeLLM()
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
        yield client, fake_llm, fake_speech, int(admin["id"]), int(learner["id"])


def _approve_first_lesson(client: TestClient, admin_id: int) -> int:
    """Approve the very first lesson's plan and return its lesson id."""
    topics = client.get("/api/curriculum/topics").json()
    lesson_id = topics[0]["lessons"][0]["id"]
    headers = {"X-User-Id": str(admin_id)}
    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "Greet warmly. Ask their name. Reply with なまえは…"},
        headers=headers,
    )
    client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=headers)
    return int(lesson_id)


# --------------------------------------------------------------------------- #
# Active / next-lesson
# --------------------------------------------------------------------------- #


def test_active_returns_null_and_no_next_when_no_plans(session_setup) -> None:
    client, _, _, _, learner_id = session_setup
    body = client.get("/api/sessions/active", headers={"X-User-Id": str(learner_id)}).json()
    assert body["active"] is None
    assert body["next_lesson"] is None


def test_next_lesson_appears_once_a_plan_is_approved(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    lesson_id = _approve_first_lesson(client, admin_id)
    body = client.get("/api/sessions/active", headers={"X-User-Id": str(learner_id)}).json()
    assert body["active"] is None
    assert body["next_lesson"]["id"] == lesson_id
    assert body["next_lesson"]["level"] in {"A1", "A2", "B1"}


# --------------------------------------------------------------------------- #
# Start / persist / resume
# --------------------------------------------------------------------------- #


def test_start_session_creates_session_with_opening_turn(session_setup) -> None:
    client, llm, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    llm.next_replies = ["こんにちは!元気ですか?"]

    response = client.post(
        "/api/sessions/start",
        json={},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["session"]["user_id"] == learner_id
    assert body["session"]["ended_at"] is None
    assert body["lesson"]["title_en"]
    assert len(body["turns"]) == 1
    assert body["turns"][0]["role"] == "assistant"
    assert body["turns"][0]["text"] == "こんにちは!元気ですか?"

    # The system prompt for the opener includes the lesson title and plan.
    [(_, system_prompt)] = llm.calls
    assert "Saying hi" in system_prompt or "lesson" in system_prompt.lower()
    assert "Greet warmly" in system_prompt


def test_active_returns_session_with_turns_after_start(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()

    body = client.get(
        "/api/sessions/active", headers={"X-User-Id": str(learner_id)}
    ).json()
    assert body["active"] is not None
    assert body["active"]["session"]["id"] == started["session"]["id"]
    assert len(body["active"]["turns"]) == 1


def test_text_turn_appends_user_and_assistant(session_setup) -> None:
    client, llm, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    llm.next_replies = ["opening greeting", "そうなんですね!"]
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]

    response = client.post(
        f"/api/sessions/{session_id}/turn",
        json={"content": "やあ"},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    roles = [t["role"] for t in body["turns"]]
    assert roles == ["assistant", "user", "assistant"]
    assert body["turns"][1]["text"] == "やあ"
    assert body["turns"][2]["text"] == "そうなんですね!"


def test_voice_turn_persists_audio_and_transcript(session_setup) -> None:
    client, llm, speech, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    llm.next_replies = ["opening", "なるほど"]
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]
    speech.transcript = "おなかがすいた"

    response = client.post(
        f"/api/sessions/{session_id}/turn-audio",
        files={"audio": ("rec.webm", io.BytesIO(b"audio-bytes"), "audio/webm")},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["turns"][-2]["text"] == "おなかがすいた"
    assert body["turns"][-2]["role"] == "user"
    assert body["turns"][-1]["role"] == "assistant"
    assert body["turns"][-1]["audio_url"]
    assert body["turns"][-1]["audio_url"].endswith(".mp3")

    # The audio URL is also fetchable.
    audio_resp = client.get(body["turns"][-1]["audio_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.content == b"FAKE_MP3"


def test_voice_turn_passes_name_and_vocab_phrase_hints(session_setup) -> None:
    client, _, speech, admin_id, learner_id = session_setup
    learner_headers = {"X-User-Id": str(learner_id)}
    # Give the learner a Japanese name so STT can be biased toward it.
    client.patch(f"/api/users/{learner_id}", json={"name_ja": "ソラ"})

    # Approve a lesson whose plan carries a Target vocabulary section.
    topics = client.get("/api/curriculum/topics").json()
    lesson_id = topics[0]["lessons"][0]["id"]
    admin_headers = {"X-User-Id": str(admin_id)}
    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "## Target vocabulary\n- **はじめまして** (x) — hi\n"},
        headers=admin_headers,
    )
    client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=admin_headers)

    started = client.post(
        "/api/sessions/start", json={"lesson_id": lesson_id}, headers=learner_headers
    ).json()
    client.post(
        f"/api/sessions/{started['session']['id']}/turn-audio",
        files={"audio": ("rec.webm", io.BytesIO(b"audio-bytes"), "audio/webm")},
        headers=learner_headers,
    )

    # Name goes in the strong (max-boost) context; lesson vocab in phrase_hints.
    assert speech.last_strong_hints == ["ソラ"]
    assert speech.last_phrase_hints is not None
    assert "はじめまして" in speech.last_phrase_hints  # lesson vocab
    assert "ソラ" not in speech.last_phrase_hints  # not duplicated in vocab


def test_end_session_sets_ended_at(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]

    ended = client.post(
        f"/api/sessions/{session_id}/end", headers={"X-User-Id": str(learner_id)}
    ).json()
    assert ended["ended_at"] is not None

    # /active should now skip this ended session.
    body = client.get(
        "/api/sessions/active", headers={"X-User-Id": str(learner_id)}
    ).json()
    assert body["active"] is None


def test_text_turn_400_on_ended_session(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]
    client.post(f"/api/sessions/{session_id}/end", headers={"X-User-Id": str(learner_id)})

    response = client.post(
        f"/api/sessions/{session_id}/turn",
        json={"content": "hi"},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 400


def test_cannot_access_another_users_session(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]

    other = client.post("/api/users", json={"name": "Other"}).json()
    response = client.get(
        f"/api/sessions/{session_id}", headers={"X-User-Id": str(other["id"])}
    )
    assert response.status_code == 403


def test_start_400_when_no_approved_plans(session_setup) -> None:
    client, _, _, _, learner_id = session_setup
    response = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    )
    assert response.status_code == 400


def test_start_with_explicit_lesson_id(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    lesson_id = _approve_first_lesson(client, admin_id)
    response = client.post(
        "/api/sessions/start",
        json={"lesson_id": lesson_id},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 201
    assert response.json()["lesson"]["id"] == lesson_id


def test_picker_skips_completed_lessons(session_setup) -> None:
    """After ending a session for lesson A, /next-lesson points to a different lesson."""
    client, _, _, admin_id, learner_id = session_setup
    # Approve two lessons.
    topics = client.get("/api/curriculum/topics").json()
    lesson_a = topics[0]["lessons"][0]["id"]
    lesson_b = topics[0]["lessons"][1]["id"]
    headers = {"X-User-Id": str(admin_id)}
    for lid in (lesson_a, lesson_b):
        client.put(
            f"/api/curriculum/lessons/{lid}/plan",
            json={"body_markdown": "ok"},
            headers=headers,
        )
        client.post(f"/api/curriculum/lessons/{lid}/plan/approve", headers=headers)

    # First pick is lesson A (first by sort order).
    next_a = client.get(
        "/api/sessions/next-lesson", headers={"X-User-Id": str(learner_id)}
    ).json()
    assert next_a["id"] == lesson_a

    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    client.post(
        f"/api/sessions/{started['session']['id']}/end",
        headers={"X-User-Id": str(learner_id)},
    )

    # After completing A, the picker should advance to B.
    next_b = client.get(
        "/api/sessions/next-lesson", headers={"X-User-Id": str(learner_id)}
    ).json()
    assert next_b["id"] == lesson_b


def test_lesson_options_empty_when_no_plans(session_setup) -> None:
    client, _, _, _, learner_id = session_setup
    body = client.get(
        "/api/sessions/lessons", headers={"X-User-Id": str(learner_id)}
    ).json()
    assert body == []


def test_lesson_options_marks_new_and_practiced(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    lesson_id = _approve_first_lesson(client, admin_id)
    learner_headers = {"X-User-Id": str(learner_id)}

    # Freshly approved lesson: appears, never practiced.
    options = client.get("/api/sessions/lessons", headers=learner_headers).json()
    assert len(options) == 1
    assert options[0]["id"] == lesson_id
    assert options[0]["practiced_count"] == 0
    assert options[0]["last_practiced_at"] is None

    # Start + end a session on it -> now counts as practiced once.
    started = client.post(
        "/api/sessions/start", json={"lesson_id": lesson_id}, headers=learner_headers
    ).json()
    client.post(
        f"/api/sessions/{started['session']['id']}/end", headers=learner_headers
    )

    options = client.get("/api/sessions/lessons", headers=learner_headers).json()
    assert options[0]["practiced_count"] == 1
    assert options[0]["last_practiced_at"] is not None


def test_lesson_study_returns_learner_sections_only(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    topics = client.get("/api/curriculum/topics").json()
    lesson_id = topics[0]["lessons"][0]["id"]
    admin_headers = {"X-User-Id": str(admin_id)}
    body_md = (
        "## Scenario\nAt the host home.\n\n"
        "## Target vocabulary\n- こんにちは\n\n"
        "## Key sentence patterns\n- はじめまして\n\n"
        "## Example exchange\n「Tutor:」spoiler\n\n"
        "## Tutor notes\n- watch particles\n"
    )
    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": body_md},
        headers=admin_headers,
    )
    client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=admin_headers)

    resp = client.get(
        f"/api/sessions/lessons/{lesson_id}/study", headers={"X-User-Id": str(learner_id)}
    )
    assert resp.status_code == 200
    md = resp.json()["study_markdown"]
    assert "Scenario" in md and "Target vocabulary" in md and "Key sentence patterns" in md
    # Tutor-only sections are stripped (no spoilers / meta guidance).
    assert "Example exchange" not in md and "spoiler" not in md
    assert "Tutor notes" not in md


def test_lesson_study_404_without_approved_plan(session_setup) -> None:
    client, _, _, _, learner_id = session_setup
    topics = client.get("/api/curriculum/topics").json()
    lesson_id = topics[0]["lessons"][0]["id"]  # never approved in this test
    resp = client.get(
        f"/api/sessions/lessons/{lesson_id}/study", headers={"X-User-Id": str(learner_id)}
    )
    assert resp.status_code == 404


def test_lesson_options_are_per_user(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    lesson_id = _approve_first_lesson(client, admin_id)

    # Learner practices; the admin's view of the same lesson stays "new".
    learner_headers = {"X-User-Id": str(learner_id)}
    started = client.post(
        "/api/sessions/start", json={"lesson_id": lesson_id}, headers=learner_headers
    ).json()
    client.post(
        f"/api/sessions/{started['session']['id']}/end", headers=learner_headers
    )

    admin_options = client.get(
        "/api/sessions/lessons", headers={"X-User-Id": str(admin_id)}
    ).json()
    assert admin_options[0]["practiced_count"] == 0


def test_start_with_three_phase_mode_records_mode_and_in_prompt(session_setup) -> None:
    client, llm, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    response = client.post(
        "/api/sessions/start",
        json={"mode": "three_phase"},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session"]["mode"] == "three_phase"
    # System prompt mentions the three-phase structure.
    [(_, system_prompt)] = llm.calls
    assert "three phases" in system_prompt.lower()


def test_end_of_session_correction_style_generates_summary(session_setup) -> None:
    client, llm, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    # Switch the learner to end_of_session.
    client.patch(
        f"/api/users/{learner_id}",
        json={"correction_style": "end_of_session"},
    )
    llm.next_replies = ["opening", "そうですね", "Here are 2 corrections: …"]

    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]
    client.post(
        f"/api/sessions/{session_id}/turn",
        json={"content": "やあ"},
        headers={"X-User-Id": str(learner_id)},
    )
    ended = client.post(
        f"/api/sessions/{session_id}/end",
        headers={"X-User-Id": str(learner_id)},
    ).json()
    assert ended["summary"] is not None
    assert "corrections" in ended["summary"].lower()


def test_end_of_turn_correction_style_does_not_generate_summary(session_setup) -> None:
    client, llm, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    # Default correction_style is end_of_turn.
    llm.next_replies = ["opening", "そうですね"]
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = started["session"]["id"]
    client.post(
        f"/api/sessions/{session_id}/turn",
        json={"content": "やあ"},
        headers={"X-User-Id": str(learner_id)},
    )
    ended = client.post(
        f"/api/sessions/{session_id}/end",
        headers={"X-User-Id": str(learner_id)},
    ).json()
    assert ended["summary"] is None


def test_reading_aids_parsed_into_turn_fields(session_setup) -> None:
    client, llm, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    # Enable both reading aids on the learner.
    client.patch(
        f"/api/users/{learner_id}",
        json={"show_hiragana": True, "show_english": True},
    )
    # Mock the LLM to emit the structured format.
    llm.next_replies = [
        "こんにちは!\n[HIRAGANA] こんにちは!\n[EN] Hello!",
        "そうですね、いいですね!\n[HIRAGANA] そうですね、いいですね!\n[EN] Right, that's nice!",
    ]
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    opening = started["turns"][-1]
    assert opening["text"] == "こんにちは!"
    assert opening["hiragana"] == "こんにちは!"
    assert opening["english"] == "Hello!"

    response = client.post(
        f"/api/sessions/{started['session']['id']}/turn",
        json={"content": "やあ"},
        headers={"X-User-Id": str(learner_id)},
    ).json()
    last_assistant = response["turns"][-1]
    assert last_assistant["role"] == "assistant"
    assert last_assistant["text"] == "そうですね、いいですね!"
    assert last_assistant["hiragana"] == "そうですね、いいですね!"
    assert last_assistant["english"] == "Right, that's nice!"


# --------------------------------------------------------------------------- #
# Reading-aid backfill (_ensure_reading_aids)
# --------------------------------------------------------------------------- #


def test_ensure_reading_aids_backfills_when_missing() -> None:
    llm = FakeLLM()
    llm.next_replies = ["[HIRAGANA] こんにちは\n[EN] Hello"]
    user = {"show_hiragana": 1, "show_english": 1}
    parsed = ParsedReply(text="今日は。", hiragana=None, english=None)

    result = _ensure_reading_aids(llm, user, parsed)

    assert result.text == "今日は。"
    assert result.hiragana == "こんにちは"
    assert result.english == "Hello"
    assert len(llm.calls) == 1  # one focused backfill call


def test_ensure_reading_aids_skips_when_already_present() -> None:
    llm = FakeLLM()
    user = {"show_hiragana": 1, "show_english": 1}
    parsed = ParsedReply(text="x", hiragana="ひ", english="e")

    result = _ensure_reading_aids(llm, user, parsed)

    assert result == parsed
    assert llm.calls == []  # no extra LLM call when nothing is missing


def test_ensure_reading_aids_skips_when_not_requested() -> None:
    llm = FakeLLM()
    user = {"show_hiragana": 0, "show_english": 0}
    parsed = ParsedReply(text="x", hiragana=None, english=None)

    result = _ensure_reading_aids(llm, user, parsed)

    assert result == parsed
    assert llm.calls == []


def test_ensure_reading_aids_only_backfills_the_missing_one() -> None:
    llm = FakeLLM()
    llm.next_replies = ["[EN] Hello there"]
    # Wants both; hiragana already present, English missing.
    user = {"show_hiragana": 1, "show_english": 1}
    parsed = ParsedReply(text="今日は。", hiragana="きょうは。", english=None)

    result = _ensure_reading_aids(llm, user, parsed)

    assert result.hiragana == "きょうは。"  # untouched
    assert result.english == "Hello there"  # backfilled


def test_ensure_reading_aids_is_best_effort_on_error() -> None:
    class BoomLLM(FakeLLM):
        def chat(self, messages, *, system, images=None, temperature=0.6, max_tokens=None):  # noqa: ARG002
            raise RuntimeError("provider down")

    parsed = ParsedReply(text="今日は。", hiragana=None, english=None)
    result = _ensure_reading_aids(BoomLLM(), {"show_english": 1}, parsed)

    # A failed aid call must not blow up the turn — just returns what we had.
    assert result == parsed


# --------------------------------------------------------------------------- #
# Robust reply generation (_tutor_reply)
# --------------------------------------------------------------------------- #


def test_tutor_reply_returns_first_nonempty() -> None:
    llm = FakeLLM()
    llm.next_replies = ["こんにちは"]
    out = _tutor_reply(llm, [Message(role="user", content="やあ")], "sys")
    assert out == "こんにちは"
    assert len(llm.calls) == 1


def test_tutor_reply_retries_once_on_empty() -> None:
    llm = FakeLLM()
    llm.next_replies = ["", "リトライ"]  # first empty, second good
    out = _tutor_reply(llm, [Message(role="user", content="やあ")], "sys")
    assert out == "リトライ"
    assert len(llm.calls) == 2


def test_tutor_reply_falls_back_when_both_empty() -> None:
    llm = FakeLLM()
    llm.next_replies = ["", ""]
    out = _tutor_reply(llm, [Message(role="user", content="やあ")], "sys")
    assert out == _FALLBACK_REPLY  # a real sentence, never "…"
    assert out != "…"
    assert len(llm.calls) == 2


def test_tutor_reply_drops_trailing_assistant_prefill() -> None:
    # Sonnet 5+ 400s if the request ends with an assistant turn.
    llm = FakeLLM()
    llm.next_replies = ["ok"]
    history = [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1"),
    ]
    _tutor_reply(llm, history, "sys")
    sent_msgs, _ = llm.calls[0]
    assert sent_msgs[-1].role == "user"
    assert len(sent_msgs) == 1


# --------------------------------------------------------------------------- #
# Reset learning progress
# --------------------------------------------------------------------------- #


def test_reset_progress_clears_history_but_keeps_profile(session_setup) -> None:
    client, _, _, admin_id, learner_id = session_setup
    _approve_first_lesson(client, admin_id)
    learner_headers = {"X-User-Id": str(learner_id)}

    # Build some progress: a finished session + turns, and a topic interest.
    started = client.post(
        "/api/sessions/start", json={}, headers=learner_headers
    ).json()
    client.post(
        f"/api/sessions/{started['session']['id']}/turn",
        json={"content": "やあ"},
        headers=learner_headers,
    )
    client.post(f"/api/sessions/{started['session']['id']}/end", headers=learner_headers)
    from sqlalchemy import insert, select

    with get_engine().begin() as conn:
        conn.execute(
            insert(topic_interests_table).values(
                user_id=learner_id, keyword="soccer", weight=3
            )
        )

    # Sanity: the lesson now shows as practiced.
    opts = client.get("/api/sessions/lessons", headers=learner_headers).json()
    assert opts[0]["practiced_count"] >= 1

    # Reset.
    resp = client.post(f"/api/users/{learner_id}/reset-progress")
    assert resp.status_code == 200, resp.json()
    cleared = resp.json()["cleared"]
    assert cleared["sessions"] >= 1
    assert cleared["turns"] >= 1
    assert cleared["interests"] == 1

    # Profile still exists with its settings intact.
    user = client.get(f"/api/users/{learner_id}").json()
    assert user["name"] == "Sora"

    # Looks fresh again: no active session, lesson back to "new", interests gone.
    active = client.get("/api/sessions/active", headers=learner_headers).json()
    assert active["active"] is None
    opts = client.get("/api/sessions/lessons", headers=learner_headers).json()
    assert opts[0]["practiced_count"] == 0
    with get_engine().connect() as conn:
        remaining = conn.execute(
            select(topic_interests_table).where(
                topic_interests_table.c.user_id == learner_id
            )
        ).all()
    assert remaining == []


def test_reset_progress_404_for_unknown_user(session_setup) -> None:
    client, _, _, _, _ = session_setup
    assert client.post("/api/users/99999/reset-progress").status_code == 404
