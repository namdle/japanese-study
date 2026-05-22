"""Pydantic schemas for User CRUD."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Allowed values for the small enum-style fields.
TutorVoice = Literal["Misa", "Hiro"]
LLMProvider = Literal["claude", "gemini", "openai", "bedrock"]
SpeechProvider = Literal["gcloud", "openai"]
CorrectionStyle = Literal["end_of_turn", "end_of_session"]
ExplanationLanguage = Literal["en", "ja"]
ProficiencyLevel = Literal["A1", "A2", "B1", "B2", "C1"]


def _normalized_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Name is required")
    if len(cleaned) > 60:
        raise ValueError("Name must be 60 characters or fewer")
    return cleaned


class UserOut(BaseModel):
    """Response shape for a single profile."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_admin: bool
    level: ProficiencyLevel
    voice: TutorVoice
    llm_provider: LLMProvider
    speech_provider: SpeechProvider
    correction_style: CorrectionStyle
    explanation_language: ExplanationLanguage
    show_hiragana: bool
    show_english: bool
    created_at: datetime


class UserCreate(BaseModel):
    """Body for POST /api/users.

    Mirrors kana-flash's minimal create shape: only `name` is required;
    everything else takes sensible defaults.
    """

    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalized_name(value)


class UserUpdate(BaseModel):
    """Body for PATCH /api/users/{id}. All fields optional."""

    name: str | None = None
    is_admin: bool | None = None
    level: ProficiencyLevel | None = None
    voice: TutorVoice | None = None
    llm_provider: LLMProvider | None = None
    speech_provider: SpeechProvider | None = None
    correction_style: CorrectionStyle | None = None
    explanation_language: ExplanationLanguage | None = None
    show_hiragana: bool | None = None
    show_english: bool | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _normalized_name(value)


class OkResponse(BaseModel):
    ok: bool = Field(default=True)
