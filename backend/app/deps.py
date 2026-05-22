"""FastAPI dependencies for the trust-based profile model.

The auth posture is intentionally lightweight: the browser remembers the
selected profile in localStorage and sends `X-User-Id` with each request.
There are no passwords. When the app is exposed beyond the LAN, the auth
boundary lives at the edge (Cloudflare Access).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db import get_engine, users_table

# Header name used by the frontend for profile selection.
USER_ID_HEADER = "X-User-Id"


def _engine_dep() -> Engine:
    return get_engine()


EngineDep = Annotated[Engine, Depends(_engine_dep)]


def current_user(
    engine: EngineDep,
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> Mapping[str, object]:
    """Resolve the current user from the X-User-Id header.

    Returns a row mapping (id, name, is_admin, ...). Raises 401 if the
    header is missing or the user does not exist. The frontend prompts the
    user to pick a profile when this happens.
    """
    if x_user_id is None or not x_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required. Pick a profile first.",
        )
    try:
        user_id = int(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id must be an integer",
        ) from exc

    with engine.connect() as conn:
        row = conn.execute(
            select(users_table).where(users_table.c.id == user_id)
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unknown profile id {user_id}",
        )
    return row


CurrentUser = Annotated[Mapping[str, object], Depends(current_user)]


def require_admin(user: CurrentUser) -> Mapping[str, object]:
    """Gate a route on the current user's is_admin flag."""
    if not user["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return user


AdminUser = Annotated[Mapping[str, object], Depends(require_admin)]
