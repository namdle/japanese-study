"""Text chat endpoint.

Stateless for now — the frontend keeps the running message log. Task 8
introduces persistent sessions.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.deps import CurrentUser
from app.llm.base import Message, build_tutor_system_prompt
from app.llm.router import (
    ProviderUnavailableError,
    UnknownProviderError,
    get_provider_for_user,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class ChatReply(BaseModel):
    reply: str
    voice: str
    provider: str


# Provider lookup is wrapped in a dependency so tests can override it.
def get_provider_for_user_dep(user: CurrentUser):
    try:
        return get_provider_for_user(user)
    except ProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("", response_model=ChatReply)
def chat(
    payload: ChatRequest,
    user: CurrentUser,
    provider: Annotated[object, Depends(get_provider_for_user_dep)],
) -> ChatReply:
    # Last message must be from the user — guard against accidental misuse.
    if payload.messages[-1].role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The last message must be from the user.",
        )

    system_prompt = build_tutor_system_prompt(user)
    history = [Message(role=m.role, content=m.content) for m in payload.messages]

    try:
        result = provider.chat(history, system=system_prompt)  # type: ignore[attr-defined]
    except ProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {exc}",
        ) from exc

    return ChatReply(
        reply=result.text,
        voice=str(user["voice"]),
        provider=getattr(provider, "name", "unknown"),
    )
