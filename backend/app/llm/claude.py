"""Anthropic Claude adapter for the LLMProvider interface.

The SDK reads ANTHROPIC_API_KEY from the environment by default. We keep
the adapter sync because FastAPI runs sync endpoints in a threadpool, so
we don't block the event loop.
"""

from __future__ import annotations

import base64
import os

import anthropic

from app.llm.base import ChatResponse, LLMProvider, Message
from app.session.uploads import detect_image_mime

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024


class ProviderUnavailableError(RuntimeError):
    """Raised when the provider is not configured (e.g., missing API key)."""


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if not resolved_key:
                raise ProviderUnavailableError(
                    "Claude provider is not configured. Set ANTHROPIC_API_KEY."
                )
            self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = model
        self._max_tokens = max_tokens

    def chat(
        self,
        messages: list[Message],
        *,
        system: str,
        images: list[bytes] | None = None,
        temperature: float = 0.6,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        anthropic_messages: list[dict] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        # Attach any images to the last user message as content blocks. The
        # caller is responsible for ensuring the last message is from the
        # user when sending images.
        if images and anthropic_messages and anthropic_messages[-1]["role"] == "user":
            text_part = {"type": "text", "text": anthropic_messages[-1]["content"]}
            image_parts: list[dict] = []
            for raw in images:
                mime = detect_image_mime(raw) or "image/jpeg"
                image_parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    }
                )
            anthropic_messages[-1] = {
                "role": "user",
                "content": [*image_parts, text_part],
            }

        response = self._client.messages.create(
            model=self._model,
            system=system,
            messages=anthropic_messages,
            max_tokens=max_tokens or self._max_tokens,
        )
        text = "".join(
            getattr(block, "text", "") for block in response.content
        ).strip()
        return ChatResponse(text=text)
