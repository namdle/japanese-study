"""Integration tests for POST /api/chat.

We override the FastAPI dependency that resolves the LLM provider so no
real provider SDK calls are made.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_provider_for_user_dep
from app.config import Settings
from app.db import reset_engine_for_tests
from app.deps import CurrentUser
from app.llm.base import ChatResponse, Message
from app.main import create_app


class FakeProvider:
    name = "fake"

    def __init__(self, reply: str = "もしもし!") -> None:
        self.reply = reply
        self.calls: list[tuple[list[Message], str]] = []

    def chat(
        self,
        messages: list[Message],
        *,
        system: str,
        images=None,  # noqa: ARG002 - matches LLMProvider shape
        temperature: float = 0.6,  # noqa: ARG002
    ) -> ChatResponse:
        self.calls.append((list(messages), system))
        return ChatResponse(text=self.reply)


@pytest.fixture
def chat_setup(
    settings: Settings,  # noqa: ARG001 - fixture activates env override
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,  # noqa: ARG001
) -> Iterator[tuple[TestClient, FakeProvider, int]]:
    """Build an app, register the fake provider, seed a user, and return (client, fake, user_id)."""
    monkeypatch.setenv("APP_DATA_DIR", str(settings.data_dir))
    reset_engine_for_tests()
    app = create_app()
    fake = FakeProvider(reply="こんにちは、Soraさん!")

    # FastAPI inspects the override function's signature; we mirror the original
    # dep's signature so it doesn't treat `user` as a query parameter.
    def _override(user: CurrentUser):  # noqa: ARG001 - signature must match
        return fake

    app.dependency_overrides[get_provider_for_user_dep] = _override

    with TestClient(app) as client:
        # Seed a user via the public API (also exercises the create flow).
        created = client.post("/api/users", json={"name": "Sora"}).json()
        yield client, fake, created["id"]


def _post_chat(client: TestClient, user_id: int, messages: list[dict]) -> dict:
    return client.post(
        "/api/chat",
        json={"messages": messages},
        headers={"X-User-Id": str(user_id)},
    ).json()


def test_chat_returns_provider_reply(chat_setup) -> None:
    client, fake, user_id = chat_setup
    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "やあ"}]},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == fake.reply
    assert body["voice"] == "Misa"
    assert body["provider"] == "fake"

    # The provider was called with the system prompt referencing the tutor and learner.
    [(messages, system)] = fake.calls
    assert messages == [Message(role="user", content="やあ")]
    assert "Misa" in system
    assert "Sora" in system


def test_chat_requires_x_user_id(chat_setup) -> None:
    client, _, _ = chat_setup
    response = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 401


def test_chat_400_when_last_message_not_user(chat_setup) -> None:
    client, _, user_id = chat_setup
    response = client.post(
        "/api/chat",
        json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hi back"},
            ]
        },
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 400


def test_chat_422_when_messages_empty(chat_setup) -> None:
    client, _, user_id = chat_setup
    response = client.post(
        "/api/chat",
        json={"messages": []},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 422


def test_chat_carries_history_to_provider(chat_setup) -> None:
    client, fake, user_id = chat_setup
    _post_chat(
        client,
        user_id,
        [
            {"role": "user", "content": "やあ"},
            {"role": "assistant", "content": "こんにちは"},
            {"role": "user", "content": "元気?"},
        ],
    )
    [(messages, _)] = fake.calls
    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[-1].content == "元気?"


def test_chat_503_when_provider_unavailable(chat_setup, monkeypatch) -> None:  # noqa: ARG001
    client, _, user_id = chat_setup
    # Replace the override with one that raises ProviderUnavailableError.
    from app.llm.claude import ProviderUnavailableError

    def boom(user: CurrentUser):  # noqa: ARG001 - signature must match
        class P:
            name = "claude"

            def chat(self, *_a, **_kw):
                raise ProviderUnavailableError("Set ANTHROPIC_API_KEY")

        return P()

    client.app.dependency_overrides[get_provider_for_user_dep] = boom

    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"X-User-Id": str(user_id)},
    )
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
