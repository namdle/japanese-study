"""Tests for curriculum seed + /api/curriculum endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.curriculum.seed import LESSONS, TOPICS, seed_curriculum
from app.db import get_engine, lessons_table, topics_table

# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def test_init_db_seeds_topics_and_lessons(client: TestClient) -> None:  # noqa: ARG001
    engine = get_engine()
    with engine.connect() as conn:
        topic_count = conn.execute(select(topics_table)).fetchall()
        lesson_count = conn.execute(select(lessons_table)).fetchall()
    assert len(topic_count) == len(TOPICS)
    assert len(lesson_count) == len(LESSONS)


def test_seed_is_idempotent(client: TestClient) -> None:  # noqa: ARG001
    engine = get_engine()
    inserted_again = seed_curriculum(engine)
    assert inserted_again == {"topics": 0, "lessons": 0}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_list_topics_returns_lessons_nested(client: TestClient) -> None:
    response = client.get("/api/curriculum/topics")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(TOPICS)
    # Topics are sorted by sort_order; first topic is Greetings.
    assert body[0]["code"] == "T01_GREETINGS"
    # Each topic has 4 nested lessons (two A1, one A2, one B1).
    for t in body:
        assert len(t["lessons"]) == 4
        levels = sorted(lesson["level"] for lesson in t["lessons"])
        assert levels == ["A1", "A1", "A2", "B1"]


def test_get_lesson_returns_can_dos_and_no_plan_initially(client: TestClient) -> None:
    topics = client.get("/api/curriculum/topics").json()
    lesson = topics[0]["lessons"][0]
    response = client.get(f"/api/curriculum/lessons/{lesson['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["lesson"]["id"] == lesson["id"]
    assert isinstance(body["lesson"]["can_dos"], list)
    assert len(body["lesson"]["can_dos"]) >= 1
    assert body["plan"] is None


def test_get_lesson_404_when_unknown(client: TestClient) -> None:
    assert client.get("/api/curriculum/lessons/999999").status_code == 404


# --------------------------------------------------------------------------- #
# Writes (admin only)
# --------------------------------------------------------------------------- #


def _admin_user(client: TestClient) -> int:
    """Create an admin profile and return its id."""
    user = client.post("/api/users", json={"name": "Mom"}).json()
    client.patch(f"/api/users/{user['id']}", json={"is_admin": True})
    return int(user["id"])


def _learner_user(client: TestClient) -> int:
    user = client.post("/api/users", json={"name": "Kid"}).json()
    return int(user["id"])


def _first_lesson_id(client: TestClient) -> int:
    topics = client.get("/api/curriculum/topics").json()
    return int(topics[0]["lessons"][0]["id"])


def test_save_plan_creates_draft(client: TestClient) -> None:
    admin_id = _admin_user(client)
    lesson_id = _first_lesson_id(client)

    response = client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "# Greet warmly\n\nUse こんにちは."},
        headers={"X-User-Id": str(admin_id)},
    )
    assert response.status_code == 200, response.json()
    plan = response.json()
    assert plan["status"] == "draft"
    assert plan["body_markdown"].startswith("# Greet warmly")
    assert plan["version"] == 1
    assert plan["updated_by"] == admin_id


def test_save_plan_increments_version_and_resets_to_draft(client: TestClient) -> None:
    admin_id = _admin_user(client)
    lesson_id = _first_lesson_id(client)
    headers = {"X-User-Id": str(admin_id)}

    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "v1"},
        headers=headers,
    )
    client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=headers)
    edited = client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "v2"},
        headers=headers,
    ).json()
    assert edited["status"] == "draft"  # editing knocks an approved plan back to draft
    assert edited["body_markdown"] == "v2"
    assert edited["version"] >= 3  # create=1, approve=2, save=3


def test_approve_requires_existing_non_empty_draft(client: TestClient) -> None:
    admin_id = _admin_user(client)
    lesson_id = _first_lesson_id(client)
    headers = {"X-User-Id": str(admin_id)}

    # Approving with no plan yet -> 400
    no_plan = client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=headers)
    assert no_plan.status_code == 400

    # Saving an empty body, then trying to approve -> 400
    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "   "},
        headers=headers,
    )
    empty = client.post(f"/api/curriculum/lessons/{lesson_id}/plan/approve", headers=headers)
    assert empty.status_code == 400


def test_approve_then_revert(client: TestClient) -> None:
    admin_id = _admin_user(client)
    lesson_id = _first_lesson_id(client)
    headers = {"X-User-Id": str(admin_id)}

    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "ready"},
        headers=headers,
    )
    approved = client.post(
        f"/api/curriculum/lessons/{lesson_id}/plan/approve",
        headers=headers,
    ).json()
    assert approved["status"] == "approved"

    reverted = client.post(
        f"/api/curriculum/lessons/{lesson_id}/plan/revert",
        headers=headers,
    ).json()
    assert reverted["status"] == "draft"
    # Body is unchanged
    assert reverted["body_markdown"] == "ready"


def test_non_admin_cannot_save_plan(client: TestClient) -> None:
    learner_id = _learner_user(client)
    lesson_id = _first_lesson_id(client)
    response = client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "x"},
        headers={"X-User-Id": str(learner_id)},
    )
    assert response.status_code == 403


def test_save_plan_404_for_unknown_lesson(client: TestClient) -> None:
    admin_id = _admin_user(client)
    response = client.put(
        "/api/curriculum/lessons/999999/plan",
        json={"body_markdown": "x"},
        headers={"X-User-Id": str(admin_id)},
    )
    assert response.status_code == 404


def test_get_lesson_returns_plan_after_save(client: TestClient) -> None:
    admin_id = _admin_user(client)
    lesson_id = _first_lesson_id(client)
    client.put(
        f"/api/curriculum/lessons/{lesson_id}/plan",
        json={"body_markdown": "hello"},
        headers={"X-User-Id": str(admin_id)},
    )
    body = client.get(f"/api/curriculum/lessons/{lesson_id}").json()
    assert body["plan"]["body_markdown"] == "hello"
    assert body["plan"]["status"] == "draft"
