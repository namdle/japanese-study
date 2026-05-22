"""SessionOrchestrator: pick the next approved lesson for a learner.

Picker strategy (v1):
- Look at all lessons with an approved plan.
- Order by topic.sort_order, then lesson.sort_order.
- Skip lessons the user has already completed (i.e., has a session whose
  ended_at is set for that lesson).
- If everything is complete, fall back to the very first approved lesson so
  the learner can revisit material.

Future tasks (11/12) will use the learner profile to bias the order toward
weaker areas.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db import (
    lesson_plans_table,
    lessons_table,
    sessions_table,
    topics_table,
)


@dataclass(frozen=True)
class NextLesson:
    lesson_id: int
    lesson_code: str
    lesson_title_en: str
    lesson_title_ja: str
    lesson_level: str
    lesson_can_dos: list[str]
    topic_id: int
    topic_title_en: str
    topic_title_ja: str
    plan_id: int
    plan_body_markdown: str


def pick_next_lesson(engine: Engine, user_id: int) -> NextLesson | None:
    """Return the next approved lesson the user hasn't finished, or None.

    Returns None when no approved plans exist anywhere in the curriculum.
    """
    # All approved lessons, joined with their topic, in canonical order.
    with engine.connect() as conn:
        approved_rows = conn.execute(
            select(
                lessons_table.c.id.label("lesson_id"),
                lessons_table.c.code.label("lesson_code"),
                lessons_table.c.title_en.label("lesson_title_en"),
                lessons_table.c.title_ja.label("lesson_title_ja"),
                lessons_table.c.level.label("lesson_level"),
                lessons_table.c.can_dos_json.label("lesson_can_dos_json"),
                lessons_table.c.topic_id.label("topic_id"),
                topics_table.c.title_en.label("topic_title_en"),
                topics_table.c.title_ja.label("topic_title_ja"),
                lesson_plans_table.c.id.label("plan_id"),
                lesson_plans_table.c.body_markdown.label("plan_body_markdown"),
            )
            .select_from(
                lesson_plans_table.join(
                    lessons_table,
                    lessons_table.c.id == lesson_plans_table.c.lesson_id,
                ).join(
                    topics_table,
                    topics_table.c.id == lessons_table.c.topic_id,
                )
            )
            .where(lesson_plans_table.c.status == "approved")
            .order_by(
                topics_table.c.sort_order,
                lessons_table.c.sort_order,
                lessons_table.c.id,
            )
        ).mappings().all()

    if not approved_rows:
        return None

    # Lessons the user has already finished.
    with engine.connect() as conn:
        finished_lesson_ids = {
            r[0]
            for r in conn.execute(
                select(sessions_table.c.lesson_id)
                .where(sessions_table.c.user_id == user_id)
                .where(sessions_table.c.ended_at.is_not(None))
                .where(sessions_table.c.lesson_id.is_not(None))
            ).all()
        }

    candidate = next(
        (r for r in approved_rows if r["lesson_id"] not in finished_lesson_ids),
        approved_rows[0],  # fall back to the first if all are done
    )
    return _row_to_next_lesson(candidate)


def get_lesson_for_session(engine: Engine, lesson_id: int) -> NextLesson | None:
    """Resolve a specific lesson by id, with its approved plan, or None."""
    with engine.connect() as conn:
        row = conn.execute(
            select(
                lessons_table.c.id.label("lesson_id"),
                lessons_table.c.code.label("lesson_code"),
                lessons_table.c.title_en.label("lesson_title_en"),
                lessons_table.c.title_ja.label("lesson_title_ja"),
                lessons_table.c.level.label("lesson_level"),
                lessons_table.c.can_dos_json.label("lesson_can_dos_json"),
                lessons_table.c.topic_id.label("topic_id"),
                topics_table.c.title_en.label("topic_title_en"),
                topics_table.c.title_ja.label("topic_title_ja"),
                lesson_plans_table.c.id.label("plan_id"),
                lesson_plans_table.c.body_markdown.label("plan_body_markdown"),
            )
            .select_from(
                lesson_plans_table.join(
                    lessons_table,
                    lessons_table.c.id == lesson_plans_table.c.lesson_id,
                ).join(
                    topics_table,
                    topics_table.c.id == lessons_table.c.topic_id,
                )
            )
            .where(lessons_table.c.id == lesson_id)
            .where(lesson_plans_table.c.status == "approved")
        ).mappings().one_or_none()
    if row is None:
        return None
    return _row_to_next_lesson(row)


def _row_to_next_lesson(row: Mapping[str, object]) -> NextLesson:
    can_dos: list[str]
    try:
        can_dos = json.loads(str(row["lesson_can_dos_json"] or "[]"))
        if not isinstance(can_dos, list):
            can_dos = []
    except json.JSONDecodeError:
        can_dos = []
    return NextLesson(
        lesson_id=int(row["lesson_id"]),
        lesson_code=str(row["lesson_code"]),
        lesson_title_en=str(row["lesson_title_en"]),
        lesson_title_ja=str(row["lesson_title_ja"]),
        lesson_level=str(row["lesson_level"]),
        lesson_can_dos=can_dos,
        topic_id=int(row["topic_id"]),
        topic_title_en=str(row["topic_title_en"]),
        topic_title_ja=str(row["topic_title_ja"]),
        plan_id=int(row["plan_id"]),
        plan_body_markdown=str(row["plan_body_markdown"]),
    )
