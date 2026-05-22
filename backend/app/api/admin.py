"""Admin-only routes.

/api/admin/whoami — verify admin gate.
/api/admin/family — read-only overview of all learner profiles with stats.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db import (
    get_engine,
    grammar_points_table,
    mistakes_table,
    sessions_table,
    topic_interests_table,
    users_table,
    vocab_items_table,
)
from app.deps import AdminUser

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/whoami")
def admin_whoami(user: AdminUser) -> dict[str, object]:
    return {
        "id": user["id"],
        "name": user["name"],
        "is_admin": bool(user["is_admin"]),
    }


class FamilyMemberOut(BaseModel):
    id: int
    name: str
    level: str
    voice: str
    vocab_count: int
    grammar_count: int
    mistake_count: int
    topic_count: int
    session_count: int


@router.get("/family", response_model=list[FamilyMemberOut])
def family_overview(user: AdminUser) -> list[FamilyMemberOut]:  # noqa: ARG001
    """Return all profiles with aggregate learning stats."""
    engine = get_engine()
    with engine.connect() as conn:
        users = conn.execute(
            select(users_table).order_by(users_table.c.name)
        ).mappings().all()

    results: list[FamilyMemberOut] = []
    with engine.connect() as conn:
        for u in users:
            uid = int(u["id"])
            vocab_count = conn.execute(
                select(func.count()).where(vocab_items_table.c.user_id == uid)
            ).scalar() or 0
            grammar_count = conn.execute(
                select(func.count()).where(grammar_points_table.c.user_id == uid)
            ).scalar() or 0
            mistake_count = conn.execute(
                select(func.count()).where(mistakes_table.c.user_id == uid)
            ).scalar() or 0
            topic_count = conn.execute(
                select(func.count()).where(topic_interests_table.c.user_id == uid)
            ).scalar() or 0
            session_count = conn.execute(
                select(func.count())
                .where(sessions_table.c.user_id == uid)
                .where(sessions_table.c.ended_at.is_not(None))
            ).scalar() or 0
            results.append(
                FamilyMemberOut(
                    id=uid,
                    name=str(u["name"]),
                    level=str(u["level"]),
                    voice=str(u["voice"]),
                    vocab_count=vocab_count,
                    grammar_count=grammar_count,
                    mistake_count=mistake_count,
                    topic_count=topic_count,
                    session_count=session_count,
                )
            )
    return results


@router.get("/family/{user_id}/profile")
def family_member_profile(user_id: int, user: AdminUser):  # noqa: ARG001
    """View any learner's profile (read-only, admin only)."""
    from app.api.profile import load_profile

    engine = get_engine()
    return load_profile(engine, user_id)
