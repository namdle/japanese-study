"""Pydantic schemas for curriculum endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LessonOut(BaseModel):
    id: int
    topic_id: int
    code: str
    title_en: str
    title_ja: str
    level: str
    can_dos: list[str]
    sort_order: int


class TopicOut(BaseModel):
    id: int
    code: str
    title_en: str
    title_ja: str
    sort_order: int
    lessons: list[LessonOut] = Field(default_factory=list)


PlanStatus = Literal["draft", "approved"]


class LessonPlanOut(BaseModel):
    id: int
    lesson_id: int
    body_markdown: str
    status: PlanStatus
    version: int
    updated_at: datetime
    updated_by: int | None


class LessonDetailOut(BaseModel):
    """Lesson plus its (optional) plan."""

    lesson: LessonOut
    plan: LessonPlanOut | None


class LessonPlanWrite(BaseModel):
    body_markdown: str = Field(min_length=0, max_length=20000)
