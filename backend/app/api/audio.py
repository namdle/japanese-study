"""Serves audio files saved under {data_dir}/audio/.

We don't use FastAPI's StaticFiles mount because we want explicit
allow-listing and predictable content types.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(prefix="/api/audio", tags=["audio"])


# Allow only simple filenames (no path traversal).
ALLOWED_SUFFIXES = {".mp3", ".wav", ".opus", ".webm", ".m4a"}


def _safe_resolve(filename: str) -> Path:
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    settings = get_settings()
    audio_dir = settings.data_dir / "audio"
    target = (audio_dir / filename).resolve()
    if audio_dir.resolve() not in target.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    if target.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported audio type",
        )
    return target


@router.get("/{filename}")
def get_audio(filename: str) -> FileResponse:
    target = _safe_resolve(filename)
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found")
    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".opus": "audio/ogg",
        ".webm": "audio/webm",
        ".m4a": "audio/mp4",
    }
    return FileResponse(target, media_type=media_types[target.suffix.lower()])
