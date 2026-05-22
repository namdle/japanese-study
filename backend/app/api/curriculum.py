"""Curriculum endpoints: list topics/lessons, read/write/approve lesson plans.

Reads are open to any signed-in profile; writes (save plan, approve) require
the admin role.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import insert, select, update

from app.db import lesson_plans_table, lessons_table, topics_table
from app.deps import AdminUser, EngineDep
from app.schemas.curriculum import (
    LessonDetailOut,
    LessonOut,
    LessonPlanOut,
    LessonPlanWrite,
    TopicOut,
)

router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _row_to_lesson(row) -> LessonOut:
    data = dict(row)
    try:
        data["can_dos"] = json.loads(data.pop("can_dos_json") or "[]")
    except json.JSONDecodeError:
        data["can_dos"] = []
    return LessonOut.model_validate(data)


def _row_to_plan(row) -> LessonPlanOut:
    return LessonPlanOut.model_validate(dict(row))


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get("/topics", response_model=list[TopicOut])
def list_topics(engine: EngineDep) -> list[TopicOut]:
    with engine.connect() as conn:
        topic_rows = conn.execute(
            select(topics_table).order_by(topics_table.c.sort_order, topics_table.c.id)
        ).mappings().all()
        lesson_rows = conn.execute(
            select(lessons_table).order_by(
                lessons_table.c.topic_id,
                lessons_table.c.sort_order,
                lessons_table.c.id,
            )
        ).mappings().all()

    lessons_by_topic: dict[int, list[LessonOut]] = {}
    for r in lesson_rows:
        lessons_by_topic.setdefault(r["topic_id"], []).append(_row_to_lesson(r))

    topics: list[TopicOut] = []
    for r in topic_rows:
        topics.append(
            TopicOut(
                id=r["id"],
                code=r["code"],
                title_en=r["title_en"],
                title_ja=r["title_ja"],
                sort_order=r["sort_order"],
                lessons=lessons_by_topic.get(r["id"], []),
            )
        )
    return topics


@router.get("/lessons/{lesson_id}", response_model=LessonDetailOut)
def get_lesson(lesson_id: int, engine: EngineDep) -> LessonDetailOut:
    with engine.connect() as conn:
        lesson_row = conn.execute(
            select(lessons_table).where(lessons_table.c.id == lesson_id)
        ).mappings().one_or_none()
        if lesson_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found")
        plan_row = conn.execute(
            select(lesson_plans_table).where(lesson_plans_table.c.lesson_id == lesson_id)
        ).mappings().one_or_none()
    return LessonDetailOut(
        lesson=_row_to_lesson(lesson_row),
        plan=_row_to_plan(plan_row) if plan_row is not None else None,
    )


# --------------------------------------------------------------------------- #
# Writes (admin only)
# --------------------------------------------------------------------------- #


def _ensure_lesson_exists(engine, lesson_id: int) -> None:
    with engine.connect() as conn:
        exists = conn.execute(
            select(lessons_table.c.id).where(lessons_table.c.id == lesson_id)
        ).one_or_none()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found")


def _upsert_plan(
    engine,
    lesson_id: int,
    *,
    body_markdown: str | None = None,
    status_value: str | None = None,
    user_id: int,
) -> LessonPlanOut:
    """Insert or update the single plan row for this lesson.

    If the plan exists, increment its version and update the touched fields.
    If it does not, create a fresh draft (or with the given status_value).
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        existing = conn.execute(
            select(lesson_plans_table).where(lesson_plans_table.c.lesson_id == lesson_id)
        ).mappings().one_or_none()

        if existing is None:
            values = {
                "lesson_id": lesson_id,
                "body_markdown": body_markdown or "",
                "status": status_value or "draft",
                "version": 1,
                "updated_at": now,
                "updated_by": user_id,
            }
            result = conn.execute(insert(lesson_plans_table).values(**values))
            new_id = result.inserted_primary_key[0]
            row = conn.execute(
                select(lesson_plans_table).where(lesson_plans_table.c.id == new_id)
            ).mappings().one()
        else:
            update_values: dict[str, object] = {
                "version": existing["version"] + 1,
                "updated_at": now,
                "updated_by": user_id,
            }
            if body_markdown is not None:
                update_values["body_markdown"] = body_markdown
            if status_value is not None:
                update_values["status"] = status_value
            conn.execute(
                update(lesson_plans_table)
                .where(lesson_plans_table.c.id == existing["id"])
                .values(**update_values)
            )
            row = conn.execute(
                select(lesson_plans_table).where(lesson_plans_table.c.id == existing["id"])
            ).mappings().one()
    return _row_to_plan(row)


@router.put("/lessons/{lesson_id}/plan", response_model=LessonPlanOut)
def save_plan(
    lesson_id: int,
    payload: LessonPlanWrite,
    engine: EngineDep,
    admin: AdminUser,
) -> LessonPlanOut:
    """Save the lesson plan as a draft.

    If the plan was already approved, saving moves it back to draft so that
    changes go through an explicit re-approval step.
    """
    _ensure_lesson_exists(engine, lesson_id)
    return _upsert_plan(
        engine,
        lesson_id,
        body_markdown=payload.body_markdown,
        status_value="draft",
        user_id=int(admin["id"]),
    )


@router.post("/lessons/{lesson_id}/plan/approve", response_model=LessonPlanOut)
def approve_plan(
    lesson_id: int,
    engine: EngineDep,
    admin: AdminUser,
) -> LessonPlanOut:
    """Mark the existing draft plan as approved."""
    _ensure_lesson_exists(engine, lesson_id)
    with engine.connect() as conn:
        existing = conn.execute(
            select(lesson_plans_table).where(lesson_plans_table.c.lesson_id == lesson_id)
        ).mappings().one_or_none()
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No plan to approve. Save a draft first.",
        )
    if not existing["body_markdown"].strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve an empty plan.",
        )
    return _upsert_plan(
        engine,
        lesson_id,
        status_value="approved",
        user_id=int(admin["id"]),
    )


@router.post("/lessons/{lesson_id}/plan/revert", response_model=LessonPlanOut)
def revert_plan(
    lesson_id: int,
    engine: EngineDep,
    admin: AdminUser,
) -> LessonPlanOut:
    """Move an approved plan back to draft without changing its content."""
    _ensure_lesson_exists(engine, lesson_id)
    with engine.connect() as conn:
        existing = conn.execute(
            select(lesson_plans_table).where(lesson_plans_table.c.lesson_id == lesson_id)
        ).mappings().one_or_none()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No plan exists")
    return _upsert_plan(
        engine,
        lesson_id,
        status_value="draft",
        user_id=int(admin["id"]),
    )
