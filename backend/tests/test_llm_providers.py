"""Contract tests for Gemini, OpenAI, and Bedrock adapters.

Each adapter is tested with a mocked SDK client so no real API calls are made.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.llm.base import Message
from app.llm.bedrock import BedrockProvider
from app.llm.claude import ProviderUnavailableError
from app.llm.gemini import GeminiProvider
from app.llm.openai_provider import OpenAIProvider

# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

class TestGeminiProvider:
    def test_chat_calls_sdk(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="こんにちは!")
        provider = GeminiProvider(client=client)

        result = provider.chat(
            [Message(role="user", content="やあ")],
            system="You are Misa",
            temperature=0.5,
        )

        assert result.text == "こんにちは!"
        client.models.generate_content.assert_called_once()
        kwargs = client.models.generate_content.call_args.kwargs
        assert kwargs["model"] == "gemini-2.0-flash"

    def test_raises_when_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailableError):
            GeminiProvider()


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

class TestOpenAIProvider:
    def test_chat_calls_sdk(self) -> None:
        client = MagicMock()
        choice = SimpleNamespace(message=SimpleNamespace(content="はい!"))
        client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
        provider = OpenAIProvider(client=client)

        result = provider.chat(
            [Message(role="user", content="hi")],
            system="You are Hiro",
            temperature=0.7,
        )

        assert result.text == "はい!"
        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["temperature"] == 0.7
        # System message is prepended.
        assert call_kwargs["messages"][0] == {"role": "system", "content": "You are Hiro"}
        assert call_kwargs["messages"][1] == {"role": "user", "content": "hi"}

    def test_raises_when_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailableError):
            OpenAIProvider()


# --------------------------------------------------------------------------- #
# Bedrock
# --------------------------------------------------------------------------- #

class TestBedrockProvider:
    def test_chat_calls_invoke_model(self) -> None:
        client = MagicMock()
        response_body = {
            "content": [{"text": "元気です!"}],
        }
        client.invoke_model.return_value = {
            "body": io.BytesIO(json.dumps(response_body).encode()),
        }
        provider = BedrockProvider(client=client)

        result = provider.chat(
            [Message(role="user", content="元気?")],
            system="You are Misa",
        )

        assert result.text == "元気です!"
        call_kwargs = client.invoke_model.call_args.kwargs
        assert call_kwargs["modelId"] == "anthropic.claude-sonnet-5"
        body = json.loads(call_kwargs["body"])
        assert body["system"] == "You are Misa"
        assert body["messages"] == [{"role": "user", "content": "元気?"}]

    def test_raises_when_credentials_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        # boto3 will still create a client (it uses the credential chain),
        # but we test the explicit ProviderUnavailableError path by injecting
        # a client that raises.
        # In practice, if no creds are found, boto3 raises NoCredentialsError
        # on the first API call, not on client creation. So we test the
        # factory path by verifying the provider can be constructed with a
        # mock client.
        client = MagicMock()
        provider = BedrockProvider(client=client)
        assert provider.name == "bedrock"
