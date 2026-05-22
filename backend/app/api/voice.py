"""Voice conversation endpoint.

POST /api/voice/turn
- multipart/form-data:
    audio:   the recorded audio file (typically audio/webm or audio/mp4)
    history: JSON-encoded list of {"role": "user|assistant", "content": "..."}
- headers:
    X-User-Id
- response:
    {
      "transcript": str,        # what we heard from the user
      "reply":      str,        # tutor's text reply
      "voice":      "Misa"|"Hiro",
      "provider":   str,        # LLM provider name
      "audio_url":  "/api/audio/<file>"
    }

Generated audio is saved under {data_dir}/audio/<uuid>.mp3 and served by
the audio router (audio.py).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.chat import get_provider_for_user_dep
from app.config import get_settings
from app.deps import CurrentUser
from app.llm.base import Message, build_tutor_system_prompt
from app.speech.base import (
    SpeechProvider,
    SpeechProviderUnavailableError,
    TutorVoice,
)
from app.speech.router import (
    UnknownSpeechProviderError,
    get_speech_provider_for_user,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


def get_speech_provider_dep(user: CurrentUser) -> SpeechProvider:
    try:
        return get_speech_provider_for_user(user)
    except SpeechProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except UnknownSpeechProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


class VoiceTurnReply(BaseModel):
    transcript: str
    reply: str
    voice: str
    provider: str
    audio_url: str


def _parse_history(raw: str) -> list[Message]:
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"history must be valid JSON: {exc}",
        ) from exc
    if not isinstance(items, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="history must be a JSON array",
        )
    messages: list[Message] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"history[{i}] must be an object",
            )
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"history[{i}] must have role user|assistant and non-empty content",
            )
        messages.append(Message(role=role, content=content))
    return messages


@router.post("/turn", response_model=VoiceTurnReply)
def voice_turn(
    user: CurrentUser,
    speech: Annotated[SpeechProvider, Depends(get_speech_provider_dep)],
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
    audio: Annotated[UploadFile, File()],
    history: Annotated[str, Form()] = "[]",
) -> VoiceTurnReply:
    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="audio file is empty",
        )

    # 1) STT
    try:
        transcript = speech.transcribe(audio_bytes)
    except SpeechProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("STT failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Speech-to-text error: {exc}",
        ) from exc

    if not transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not detect any speech in the recording. Try again.",
        )

    # 2) LLM
    prior = _parse_history(history)
    full_history = [*prior, Message(role="user", content=transcript)]
    system_prompt = build_tutor_system_prompt(user)
    try:
        chat_response = llm.chat(full_history, system=system_prompt)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.exception("LLM chat failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {exc}",
        ) from exc

    reply_text = (chat_response.text or "").strip()
    if not reply_text:
        reply_text = "…"

    # 3) TTS
    voice_enum = TutorVoice.from_string(str(user["voice"]))
    try:
        synth = speech.synthesize(reply_text, voice=voice_enum)
    except Exception as exc:
        logger.exception("TTS failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Text-to-speech error: {exc}",
        ) from exc

    # 4) Persist audio under data_dir/audio/<uuid>.mp3
    settings = get_settings()
    audio_dir = settings.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    extension = "mp3" if synth.mime_type == "audio/mpeg" else "bin"
    filename = f"{uuid.uuid4().hex}.{extension}"
    (audio_dir / filename).write_bytes(synth.audio)

    return VoiceTurnReply(
        transcript=transcript,
        reply=reply_text,
        voice=str(user["voice"]),
        provider=getattr(llm, "name", "unknown"),
        audio_url=f"/api/audio/{filename}",
    )
