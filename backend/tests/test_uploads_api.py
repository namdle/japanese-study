"""Tests for image upload support: provider adapter shape + start-from-image endpoint."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_provider_for_user_dep
from app.config import Settings
from app.db import reset_engine_for_tests
from app.deps import CurrentUser
from app.llm.base import ChatResponse, Message
from app.llm.bedrock import BedrockProvider
from app.llm.claude import ClaudeProvider
from app.llm.gemini import GeminiProvider
from app.llm.openai_provider import OpenAIProvider
from app.main import create_app
from app.session.uploads import detect_image_mime

# Minimal valid JPEG bytes for the magic-number sniff.
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
# HEIC: 4 bytes box size + 'ftyp' + 'heic' brand
HEIC_BYTES = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100
# A snippet of an MP4 video (different ftyp brand) that should NOT be accepted.
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100


# --------------------------------------------------------------------------- #
# detect_image_mime
# --------------------------------------------------------------------------- #


def test_detect_jpeg() -> None:
    assert detect_image_mime(JPEG_BYTES) == "image/jpeg"


def test_detect_png() -> None:
    assert detect_image_mime(PNG_BYTES) == "image/png"


def test_detect_heic() -> None:
    assert detect_image_mime(HEIC_BYTES) == "image/heic"


def test_detect_unknown_returns_none() -> None:
    """Unknown bytes return None so the upload endpoint can reject cleanly."""
    assert detect_image_mime(b"random bytes not an image") is None


def test_detect_mp4_video_is_rejected() -> None:
    """An MP4 video (ftyp brand 'mp42') is *not* an image."""
    assert detect_image_mime(MP4_BYTES) is None


# --------------------------------------------------------------------------- #
# Provider adapters with images
# --------------------------------------------------------------------------- #


def test_claude_attaches_image_to_last_user_message() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="ok")]
    )
    provider = ClaudeProvider(client=sdk)

    provider.chat(
        [Message(role="user", content="describe this")],
        system="sys",
        images=[JPEG_BYTES],
    )
    kwargs = sdk.messages.create.call_args.kwargs
    last = kwargs["messages"][-1]
    assert last["role"] == "user"
    parts = last["content"]
    # Image part precedes the text part (Anthropic's recommended order).
    assert parts[0]["type"] == "image"
    assert parts[0]["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(parts[0]["source"]["data"]) == JPEG_BYTES
    assert parts[-1]["type"] == "text"
    assert parts[-1]["text"] == "describe this"


def test_openai_attaches_image_url_to_last_user_message() -> None:
    client = MagicMock()
    choice = SimpleNamespace(message=SimpleNamespace(content="ok"))
    client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
    provider = OpenAIProvider(client=client)

    provider.chat(
        [Message(role="user", content="hi")], system="sys", images=[PNG_BYTES]
    )
    kwargs = client.chat.completions.create.call_args.kwargs
    last_msg = kwargs["messages"][-1]
    assert last_msg["role"] == "user"
    parts = last_msg["content"]
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_gemini_attaches_image_part_to_last_user_message() -> None:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text="ok")
    provider = GeminiProvider(client=client)

    provider.chat(
        [Message(role="user", content="x")], system="sys", images=[JPEG_BYTES]
    )
    kwargs = client.models.generate_content.call_args.kwargs
    contents = kwargs["contents"]
    last_parts = contents[-1].parts
    # An image Part is prepended; the text Part follows.
    assert any(getattr(p, "inline_data", None) is not None for p in last_parts)


def test_bedrock_attaches_image_in_messages_body() -> None:
    client = MagicMock()
    response_body = {"content": [{"text": "ok"}]}
    client.invoke_model.return_value = {
        "body": io.BytesIO(json.dumps(response_body).encode())
    }
    provider = BedrockProvider(client=client)

    provider.chat(
        [Message(role="user", content="hi")], system="sys", images=[JPEG_BYTES]
    )
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    last = body["messages"][-1]
    assert last["role"] == "user"
    parts = last["content"]
    assert parts[0]["type"] == "image"
    assert parts[-1]["type"] == "text"


# --------------------------------------------------------------------------- #
# /api/sessions/start-from-image endpoint
# --------------------------------------------------------------------------- #


class FakeLLM:
    name = "fake-llm"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, messages, *, system, images=None, temperature=0.6, max_tokens=None):  # noqa: ARG002
        self.calls.append({"messages": list(messages), "system": system, "images": images})
        return ChatResponse(text="画像を見ましたね。")


@pytest.fixture
def upload_setup(
    settings: Settings,  # noqa: ARG001 - activates env override
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeLLM, int]]:
    monkeypatch.setenv("APP_DATA_DIR", str(settings.data_dir))
    reset_engine_for_tests()
    app = create_app()
    fake_llm = FakeLLM()

    def _llm(user: CurrentUser):  # noqa: ARG001
        return fake_llm

    app.dependency_overrides[get_provider_for_user_dep] = _llm

    with TestClient(app) as client:
        u = client.post("/api/users", json={"name": "Sora"}).json()
        yield client, fake_llm, int(u["id"])


def test_start_from_image_creates_session_with_seed_image_url(upload_setup) -> None:
    client, llm, user_id = upload_setup
    response = client.post(
        "/api/sessions/start-from-image",
        files={"image": ("page.jpg", io.BytesIO(JPEG_BYTES), "image/jpeg")},
        data={"mode": "freeform"},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["session"]["lesson_id"] is None
    assert body["session"]["seed_image_url"]
    assert body["session"]["seed_image_url"].startswith(f"/api/uploads/{user_id}/")
    assert body["lesson"] is None
    assert len(body["turns"]) == 1
    assert body["turns"][0]["text"] == "画像を見ましたね。"

    # The LLM was called with the image bytes.
    [call] = llm.calls
    assert call["images"] == [JPEG_BYTES]


def test_start_from_image_serves_the_uploaded_image(upload_setup) -> None:
    client, _, user_id = upload_setup
    body = client.post(
        "/api/sessions/start-from-image",
        files={"image": ("page.jpg", io.BytesIO(JPEG_BYTES), "image/jpeg")},
        data={"mode": "freeform"},
        headers={"X-User-Id": str(user_id)},
    ).json()

    img_resp = client.get(body["session"]["seed_image_url"])
    assert img_resp.status_code == 200
    assert img_resp.headers["content-type"] == "image/jpeg"
    assert img_resp.content == JPEG_BYTES


def test_start_from_image_400_when_empty(upload_setup) -> None:
    client, _, user_id = upload_setup
    response = client.post(
        "/api/sessions/start-from-image",
        files={"image": ("empty.jpg", io.BytesIO(b""), "image/jpeg")},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 400


def test_start_from_image_400_when_video_uploaded_as_image(upload_setup) -> None:
    """An MP4 with image/jpeg content-type should still be rejected."""
    client, _, user_id = upload_setup
    response = client.post(
        "/api/sessions/start-from-image",
        files={"image": ("camera.mp4", io.BytesIO(MP4_BYTES), "image/jpeg")},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "image" in detail
    assert "video" in detail or "supported" in detail


def test_start_from_image_502_when_llm_fails(upload_setup) -> None:
    """A vision-model error must not be papered over with a fake greeting."""
    client, llm, user_id = upload_setup

    def boom(messages, *, system, images=None, temperature=0.6):  # noqa: ARG001
        raise RuntimeError("vision API broken")

    llm.chat = boom  # type: ignore[assignment]
    response = client.post(
        "/api/sessions/start-from-image",
        files={"image": ("page.jpg", io.BytesIO(JPEG_BYTES), "image/jpeg")},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 502
    assert "vision API broken" in response.json()["detail"]
    # The half-created session should have been cleaned up.
    sessions = client.get(
        "/api/sessions", headers={"X-User-Id": str(user_id)}
    ).json()
    assert sessions == []


def test_uploads_endpoint_rejects_path_traversal(upload_setup) -> None:
    client, _, user_id = upload_setup
    response = client.get(f"/api/uploads/{user_id}/..%2Fjapanese.db")
    assert response.status_code in (400, 404)


def test_uploads_endpoint_rejects_bad_extension(upload_setup) -> None:
    client, _, user_id = upload_setup
    response = client.get(f"/api/uploads/{user_id}/foo.exe")
    assert response.status_code == 400
