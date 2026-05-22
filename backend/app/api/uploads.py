"""Serves uploaded textbook images from {data_dir}/uploads/<user_id>/."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_SUFFIXES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _safe_filename(part: str) -> str:
    if "/" in part or "\\" in part or part.startswith(".") or not part:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    return part


@router.get("/{user_id}/{filename}")
def get_upload(user_id: int, filename: str) -> FileResponse:
    fname = _safe_filename(filename)
    settings = get_settings()
    base = (settings.data_dir / "uploads" / str(user_id)).resolve()
    target = (base / fname).resolve()
    if base not in target.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    suffix = target.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image type",
        )
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return FileResponse(target, media_type=ALLOWED_SUFFIXES[suffix])
