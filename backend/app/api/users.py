"""User profile CRUD.

API shape mirrors kana-flash's profile management: list / create / rename /
delete by id, plus a few profile-preference fields specific to this app.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db import (
    grammar_points_table,
    mistakes_table,
    session_turns_table,
    sessions_table,
    topic_interests_table,
    users_table,
    vocab_items_table,
)
from app.deps import EngineDep
from app.schemas.users import (
    OkResponse,
    ProfileResetOut,
    UserCreate,
    UserOut,
    UserUpdate,
)

router = APIRouter(prefix="/api/users", tags=["users"])


def _row_to_user(row) -> UserOut:
    """Build a UserOut from a SQLAlchemy row mapping, coercing flags to bool."""
    data = dict(row)
    data["is_admin"] = bool(data.get("is_admin"))
    data["show_hiragana"] = bool(data.get("show_hiragana"))
    data["show_english"] = bool(data.get("show_english"))
    return UserOut.model_validate(data)


@router.get("", response_model=list[UserOut])
def list_users(engine: EngineDep) -> list[UserOut]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(users_table).order_by(users_table.c.name)
        ).mappings().all()
    return [_row_to_user(r) for r in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, engine: EngineDep) -> UserOut:
    try:
        with engine.begin() as conn:
            result = conn.execute(insert(users_table).values(name=payload.name))
            new_id = result.inserted_primary_key[0]
            row = conn.execute(
                select(users_table).where(users_table.c.id == new_id)
            ).mappings().one()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name already taken",
        ) from exc
    return _row_to_user(row)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, engine: EngineDep) -> UserOut:
    with engine.connect() as conn:
        row = conn.execute(
            select(users_table).where(users_table.c.id == user_id)
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _row_to_user(row)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, engine: EngineDep) -> UserOut:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided",
        )
    if "is_admin" in fields:
        # Convert bool -> int for SQLite storage.
        fields["is_admin"] = int(bool(fields["is_admin"]))
    if "show_hiragana" in fields:
        fields["show_hiragana"] = int(bool(fields["show_hiragana"]))
    if "show_english" in fields:
        fields["show_english"] = int(bool(fields["show_english"]))

    try:
        with engine.begin() as conn:
            result = conn.execute(
                update(users_table).where(users_table.c.id == user_id).values(**fields)
            )
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )
            row = conn.execute(
                select(users_table).where(users_table.c.id == user_id)
            ).mappings().one()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name already taken",
        ) from exc
    return _row_to_user(row)


@router.delete("/{user_id}", response_model=OkResponse)
def delete_user(user_id: int, engine: EngineDep) -> OkResponse:
    with engine.begin() as conn:
        result = conn.execute(delete(users_table).where(users_table.c.id == user_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return OkResponse()


@router.post("/{user_id}/reset-progress", response_model=ProfileResetOut)
def reset_progress(user_id: int, engine: EngineDep) -> ProfileResetOut:
    """Clear a learner's accumulated progress so the profile looks fresh.

    Wipes practice history (sessions + turns) and the adaptive learning profile
    (vocab, grammar mastery, mistakes, topic interests). Keeps the profile
    itself and all its preferences (name, voice, level, reading aids, etc.).
    """
    with engine.begin() as conn:
        exists = conn.execute(
            select(users_table.c.id).where(users_table.c.id == user_id)
        ).one_or_none()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        # Turns cascade with sessions, but delete explicitly so the count is
        # accurate and it works regardless of the SQLite FK-pragma state.
        session_ids = select(sessions_table.c.id).where(
            sessions_table.c.user_id == user_id
        )
        turns = conn.execute(
            delete(session_turns_table).where(
                session_turns_table.c.session_id.in_(session_ids)
            )
        ).rowcount
        cleared = {
            "turns": turns,
            "sessions": conn.execute(
                delete(sessions_table).where(sessions_table.c.user_id == user_id)
            ).rowcount,
            "vocab": conn.execute(
                delete(vocab_items_table).where(vocab_items_table.c.user_id == user_id)
            ).rowcount,
            "grammar": conn.execute(
                delete(grammar_points_table).where(
                    grammar_points_table.c.user_id == user_id
                )
            ).rowcount,
            "mistakes": conn.execute(
                delete(mistakes_table).where(mistakes_table.c.user_id == user_id)
            ).rowcount,
            "interests": conn.execute(
                delete(topic_interests_table).where(
                    topic_interests_table.c.user_id == user_id
                )
            ).rowcount,
        }
    return ProfileResetOut(cleared=cleared)
