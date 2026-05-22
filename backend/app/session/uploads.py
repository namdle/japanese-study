"""Helpers for saving and serving textbook image uploads.

Images are stored under {data_dir}/uploads/<user_id>/<uuid>.<ext>. The
sub-directory keeps each user's uploads tidy and lets the audio router
share the same data layout.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import get_settings

# Mapping detected mime type -> file extension for storage.
EXTENSION_FOR_MIME: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
    "image/heif": "heif",
}


def detect_image_mime(data: bytes) -> str | None:
    """Return MIME type by sniffing the file's magic bytes, or None.

    Returning None means the bytes don't match any image format we support.
    Callers should treat that as a hard rejection — sending non-image bytes
    to a vision model produces silent failures or misleading responses.
    """
    if not data or len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # HEIC/HEIF (e.g., iPhone photos): bytes 4..8 are "ftyp", and the
    # following brand is one of the HEIF family.
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"heim", b"heis"):
            return "image/heic"
        if brand in (b"mif1", b"msf1"):
            return "image/heif"
    return None


def save_upload(user_id: int, data: bytes, mime: str) -> str:
    """Persist an upload and return its path *relative to data_dir*.

    The relative path is stored on the session row; the audio/uploads
    router resolves it via data_dir again so the file location follows
    APP_DATA_DIR.
    """
    settings = get_settings()
    user_dir = settings.data_dir / "uploads" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    extension = EXTENSION_FOR_MIME.get(mime, "bin")
    filename = f"{uuid.uuid4().hex}.{extension}"
    target = user_dir / filename
    target.write_bytes(data)
    # Relative path (POSIX-style) for portability.
    return f"uploads/{user_id}/{filename}"


def absolute_upload_path(relative: str) -> Path:
    """Resolve a stored relative path back to an absolute filesystem path."""
    settings = get_settings()
    return (settings.data_dir / relative).resolve()
