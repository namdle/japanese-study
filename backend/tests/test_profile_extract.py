"""Tests for profile extraction parser, persistence, and end-of-session hook."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.chat import get_provider_for_user_dep
from app.config import Settings
from app.db import (
    grammar_points_table,
    mistakes_table,
    reset_engine_for_tests,
    topic_interests_table,
    vocab_items_table,
)
from app.deps import CurrentUser
from app.llm.base import ChatResponse
from app.main import create_app
from app.profile.extract import (
    ExtractionResult,
    parse_extraction,
    persist_extraction,
)

# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def test_parse_clean_json() -> None:
    raw = (
        '{\n'
        '  "vocab": [\n'
        '    {"jp": "ありがとう", "reading": "arigatou", "en": "thank you", '
        '"outcome": "encountered"}\n'
        '  ],\n'
        '  "grammar": [\n'
        '    {"code": "te-form", "example_jp": "食べてください", '
        '"notes": "polite request", "outcome": "used_correctly"}\n'
        '  ],\n'
        '  "mistakes": [\n'
        '    {"mistake_type": "particle", "original": "わたしは行く", '
        '"corrected": "わたしが行く", "note": "subject"}\n'
        '  ],\n'
        '  "topics": [{"keyword": "Family", "weight": 2}]\n'
        '}'
    )
    result = parse_extraction(raw)
    assert len(result.vocab) == 1
    assert result.vocab[0]["jp"] == "ありがとう"
    assert result.vocab[0]["outcome"] == "encountered"
    assert result.grammar[0]["code"] == "te-form"
    assert result.grammar[0]["outcome"] == "used_correctly"
    assert result.mistakes[0]["original"] == "わたしは行く"
    # Topic keyword is normalised to lowercase.
    assert result.topics[0]["keyword"] == "family"
    assert result.topics[0]["weight"] == 2


def test_parse_strips_markdown_fences() -> None:
    raw = '```json\n{"vocab": [], "grammar": [], "mistakes": [], "topics": []}\n```'
    result = parse_extraction(raw)
    assert result.vocab == []


def test_parse_invalid_outcome_falls_back_to_encountered() -> None:
    raw = '{"vocab": [{"jp": "犬", "outcome": "made_up_value"}]}'
    result = parse_extraction(raw)
    assert result.vocab[0]["outcome"] == "encountered"


def test_parse_skips_items_missing_required_fields() -> None:
    raw = """{
      "vocab": [{"jp": "", "outcome": "encountered"}, {"jp": "猫"}],
      "mistakes": [{"original": "x"}, {"original": "a", "corrected": "b"}]
    }"""
    result = parse_extraction(raw)
    assert [v["jp"] for v in result.vocab] == ["猫"]
    assert len(result.mistakes) == 1
    assert result.mistakes[0]["original"] == "a"


def test_parse_garbage_returns_empty_result() -> None:
    assert parse_extraction("not json at all").vocab == []
    assert parse_extraction("").vocab == []


# --------------------------------------------------------------------------- #
# Persistence + mastery clamping (uses a real client to exercise the schema)
# --------------------------------------------------------------------------- #


@pytest.fixture
def session_setup(
    settings: Settings,  # noqa: ARG001 - activates env override
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, list[str], int, int]]:
    """Build app with a chat-able fake LLM, seed admin+learner, approve a lesson."""
    monkeypatch.setenv("APP_DATA_DIR", str(settings.data_dir))
    reset_engine_for_tests()
    app = create_app()

    # Replies queued up: opening, then turn replies, then extraction JSON.
    queued_replies: list[str] = []

    class FakeLLM:
        name = "fake-llm"

        def chat(self, messages, *, system, images=None, temperature=0.6, max_tokens=None):  # noqa: ARG002
            text = queued_replies.pop(0) if queued_replies else "そうですね"
            return ChatResponse(text=text)

    fake_llm = FakeLLM()

    def _llm(user: CurrentUser):  # noqa: ARG001
        return fake_llm

    app.dependency_overrides[get_provider_for_user_dep] = _llm

    with TestClient(app) as client:
        admin = client.post("/api/users", json={"name": "Mom"}).json()
        client.patch(f"/api/users/{admin['id']}", json={"is_admin": True})
        learner = client.post("/api/users", json={"name": "Sora"}).json()

        # Approve the first lesson so the learner can start a session.
        topics = client.get("/api/curriculum/topics").json()
        lesson_id = topics[0]["lessons"][0]["id"]
        admin_headers = {"X-User-Id": str(admin["id"])}
        client.put(
            f"/api/curriculum/lessons/{lesson_id}/plan",
            json={"body_markdown": "Greet warmly."},
            headers=admin_headers,
        )
        client.post(
            f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=admin_headers
        )
        yield client, queued_replies, int(admin["id"]), int(learner["id"])


def test_persist_inserts_then_updates_with_mastery_clamping(session_setup) -> None:
    """Verify mastery rules: cap at 5 on +1, floor at 0 on -1."""
    from app.db import get_engine

    client, queued, _, learner_id = session_setup
    queued.append("こんにちは")
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = int(started["session"]["id"])
    engine = get_engine()

    # First pass: encounter a vocab item.
    extracted = ExtractionResult(
        vocab=[{"jp": "犬", "reading": "いぬ", "en": "dog", "outcome": "encountered"}],
    )
    counts = persist_extraction(engine, learner_id, session_id, extracted)
    assert counts["vocab_inserted"] == 1

    # Many correct uses: mastery should rise and cap at 5.
    for _ in range(12):
        persist_extraction(
            engine,
            learner_id,
            session_id,
            ExtractionResult(vocab=[{"jp": "犬", "outcome": "used_correctly"}]),
        )
    with engine.connect() as conn:
        row = conn.execute(
            select(vocab_items_table).where(vocab_items_table.c.jp == "犬")
        ).mappings().one()
    assert row["mastery"] == 5  # capped

    # Many mistakes: mastery should floor at 0.
    for _ in range(10):
        persist_extraction(
            engine,
            learner_id,
            session_id,
            ExtractionResult(vocab=[{"jp": "犬", "outcome": "made_mistake"}]),
        )
    with engine.connect() as conn:
        row = conn.execute(
            select(vocab_items_table).where(vocab_items_table.c.jp == "犬")
        ).mappings().one()
    assert row["mastery"] == 0  # floored


def test_persist_dedupes_grammar_and_topics(session_setup) -> None:
    from app.db import get_engine

    client, queued, _, learner_id = session_setup
    queued.append("こんにちは")
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = int(started["session"]["id"])
    engine = get_engine()
    extracted = ExtractionResult(
        grammar=[{"code": "te-form", "outcome": "used_correctly"}],
        topics=[{"keyword": "family", "weight": 1}],
    )
    persist_extraction(engine, learner_id, session_id, extracted)
    persist_extraction(engine, learner_id, session_id, extracted)
    persist_extraction(engine, learner_id, session_id, extracted)
    with engine.connect() as conn:
        gcount = len(conn.execute(select(grammar_points_table)).all())
        tcount = len(conn.execute(select(topic_interests_table)).all())
    # Single deduped row each.
    assert gcount == 1
    assert tcount == 1
    # Topic weight accumulates.
    with engine.connect() as conn:
        topic = conn.execute(select(topic_interests_table)).mappings().one()
    assert topic["weight"] == 3


def test_mistakes_are_append_only(session_setup) -> None:
    from app.db import get_engine

    client, queued, _, learner_id = session_setup
    queued.append("こんにちは")
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = int(started["session"]["id"])
    engine = get_engine()
    same_mistake = {
        "mistake_type": "particle",
        "original": "わたしは行く",
        "corrected": "わたしが行く",
        "note": "subject",
    }
    persist_extraction(
        engine, learner_id, session_id, ExtractionResult(mistakes=[same_mistake])
    )
    persist_extraction(
        engine, learner_id, session_id, ExtractionResult(mistakes=[same_mistake])
    )
    with engine.connect() as conn:
        mc = len(conn.execute(select(mistakes_table)).all())
    assert mc == 2


# --------------------------------------------------------------------------- #
# End-session hook + /api/profile read
# --------------------------------------------------------------------------- #


def test_end_session_runs_extraction_and_profile_endpoint_returns_data(session_setup) -> None:
    client, queued, _, learner_id = session_setup
    learner_headers = {"X-User-Id": str(learner_id)}

    # Start a session: opening greeting will consume one reply.
    queued.append("こんにちは!")
    started = client.post(
        "/api/sessions/start", json={}, headers=learner_headers
    ).json()
    session_id = started["session"]["id"]

    # One text turn: assistant reply consumes one reply.
    queued.append("いいですね")
    client.post(
        f"/api/sessions/{session_id}/turn",
        json={"content": "やあ"},
        headers=learner_headers,
    )

    # End session: the next reply is the extraction JSON. (correction_style
    # default is end_of_turn for new users, so no summary call is made.)
    queued.append(
        '{"vocab": [{"jp": "ありがとう", "outcome": "encountered"}], '
        '"grammar": [{"code": "te-form", "outcome": "used_correctly"}], '
        '"mistakes": [{"original": "わたしは行く", "corrected": "わたしが行く", '
        '"mistake_type": "particle", "note": "subject"}], '
        '"topics": [{"keyword": "greetings", "weight": 1}]}'
    )
    client.post(f"/api/sessions/{session_id}/end", headers=learner_headers)

    profile = client.get("/api/profile", headers=learner_headers).json()
    assert [v["jp"] for v in profile["vocab"]] == ["ありがとう"]
    assert [g["code"] for g in profile["grammar"]] == ["te-form"]
    assert [m["original"] for m in profile["mistakes"]] == ["わたしは行く"]
    assert [t["keyword"] for t in profile["topics"]] == ["greetings"]


def test_end_session_with_unparseable_extraction_does_not_break(session_setup) -> None:
    """If the LLM returns garbage, end-session still succeeds with an empty profile."""
    client, queued, _, learner_id = session_setup
    learner_headers = {"X-User-Id": str(learner_id)}

    queued.append("こんにちは!")
    started = client.post(
        "/api/sessions/start", json={}, headers=learner_headers
    ).json()
    session_id = started["session"]["id"]

    queued.append("はい")
    client.post(
        f"/api/sessions/{session_id}/turn",
        json={"content": "やあ"},
        headers=learner_headers,
    )

    queued.append("not json at all, sorry")
    response = client.post(
        f"/api/sessions/{session_id}/end", headers=learner_headers
    )
    assert response.status_code == 200
    profile = client.get("/api/profile", headers=learner_headers).json()
    assert profile["vocab"] == []
    assert profile["grammar"] == []
    assert profile["mistakes"] == []


def test_profile_endpoint_isolates_users(session_setup) -> None:
    """One learner's profile data must not leak into another's."""
    from app.db import get_engine

    client, queued, _, learner_id = session_setup
    other = client.post("/api/users", json={"name": "Other"}).json()
    queued.append("こんにちは")
    started = client.post(
        "/api/sessions/start", json={}, headers={"X-User-Id": str(learner_id)}
    ).json()
    session_id = int(started["session"]["id"])
    engine = get_engine()
    persist_extraction(
        engine,
        learner_id,
        session_id,
        ExtractionResult(vocab=[{"jp": "猫", "outcome": "encountered"}]),
    )
    sora = client.get(
        "/api/profile", headers={"X-User-Id": str(learner_id)}
    ).json()
    other_profile = client.get(
        "/api/profile", headers={"X-User-Id": str(other["id"])}
    ).json()
    assert [v["jp"] for v in sora["vocab"]] == ["猫"]
    assert other_profile["vocab"] == []
