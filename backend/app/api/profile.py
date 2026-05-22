"""Read endpoints for the learner profile (vocab, grammar, mistakes, topics).

Used by the Profile dashboard (Task 13) and by Task 12's prompt-injection
when starting a new session.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.db import (
    grammar_points_table,
    mistakes_table,
    topic_interests_table,
    vocab_items_table,
)
from app.deps import CurrentUser, EngineDep

router = APIRouter(prefix="/api/profile", tags=["profile"])


class VocabOut(BaseModel):
    id: int
    jp: str
    reading: str | None
    en: str | None
    mastery: int
    last_seen_at: datetime


class GrammarOut(BaseModel):
    id: int
    code: str
    example_jp: str | None
    notes: str | None
    mastery: int
    last_seen_at: datetime


class MistakeOut(BaseModel):
    id: int
    session_id: int | None
    mistake_type: str | None
    original: str
    corrected: str
    note: str | None
    created_at: datetime


class TopicInterestOut(BaseModel):
    id: int
    keyword: str
    weight: int
    last_seen_at: datetime


class ProfileOut(BaseModel):
    vocab: list[VocabOut]
    grammar: list[GrammarOut]
    mistakes: list[MistakeOut]
    topics: list[TopicInterestOut]


def _user_profile(engine, user_id: int) -> ProfileOut:
    with engine.connect() as conn:
        vocab_rows = conn.execute(
            select(vocab_items_table)
            .where(vocab_items_table.c.user_id == user_id)
            .order_by(vocab_items_table.c.last_seen_at.desc())
        ).mappings().all()
        grammar_rows = conn.execute(
            select(grammar_points_table)
            .where(grammar_points_table.c.user_id == user_id)
            .order_by(grammar_points_table.c.last_seen_at.desc())
        ).mappings().all()
        mistake_rows = conn.execute(
            select(mistakes_table)
            .where(mistakes_table.c.user_id == user_id)
            .order_by(mistakes_table.c.id.desc())
            .limit(50)
        ).mappings().all()
        topic_rows = conn.execute(
            select(topic_interests_table)
            .where(topic_interests_table.c.user_id == user_id)
            .order_by(topic_interests_table.c.weight.desc())
        ).mappings().all()
    return ProfileOut(
        vocab=[VocabOut.model_validate(dict(r)) for r in vocab_rows],
        grammar=[GrammarOut.model_validate(dict(r)) for r in grammar_rows],
        mistakes=[MistakeOut.model_validate(dict(r)) for r in mistake_rows],
        topics=[TopicInterestOut.model_validate(dict(r)) for r in topic_rows],
    )


@router.get("", response_model=ProfileOut)
def get_profile(user: CurrentUser, engine: EngineDep) -> ProfileOut:
    return _user_profile(engine, int(user["id"]))


# Non-FastAPI helper for callers that already have an engine handle.
def load_profile(engine, user_id: int) -> ProfileOut:
    return _user_profile(engine, user_id)
