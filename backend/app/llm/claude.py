"""Anthropic Claude adapter for the LLMProvider interface.

The SDK reads ANTHROPIC_API_KEY from the environment by default. We keep
the adapter sync because FastAPI runs sync endpoints in a threadpool, so
we don't block the event loop.

Prompt caching: the tutor system prompt (persona + lesson plan + profile
snapshot) is large and stable for the whole session, and the conversation
history only ever grows. We mark both with `cache_control` breakpoints so
2nd+ turns of a session read the prefix from Anthropic's prompt cache,
which cuts time-to-first-token noticeably. Prefixes below the model's
minimum cacheable size are silently not cached — harmless.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator

import anthropic

from app.llm.base import ChatResponse, LLMProvider, Message
from app.session.uploads import detect_image_mime

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024

_CACHE_EPHEMERAL = {"type": "ephemeral"}


class ProviderUnavailableError(RuntimeError):
    """Raised when the provider is not configured (e.g., missing API key)."""


def _system_blocks(system: str) -> list[dict]:
    """System prompt as a cacheable content block."""
    return [{"type": "text", "text": system, "cache_control": _CACHE_EPHEMERAL}]


def _build_anthropic_messages(
    messages: list[Message], images: list[bytes] | None
) -> list[dict]:
    """Convert Messages to the SDK shape, attach images, and place a cache
    breakpoint on the last message so the conversation prefix is reused on
    the next turn (history is append-only, so earlier breakpoints keep
    matching)."""
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

    if anthropic_messages:
        last = anthropic_messages[-1]
        content = last["content"]
        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content, "cache_control": _CACHE_EPHEMERAL}
            ]
        elif isinstance(content, list) and content:
            content[-1] = {**content[-1], "cache_control": _CACHE_EPHEMERAL}
    return anthropic_messages


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
        # NOTE: `temperature` is intentionally NOT forwarded — claude-sonnet-5
        # rejects non-default temperatures with a 400.
        response = self._client.messages.create(
            model=self._model,
            system=_system_blocks(system),
            messages=_build_anthropic_messages(messages, images),
            max_tokens=max_tokens or self._max_tokens,
        )
        text = "".join(
            getattr(block, "text", "") for block in response.content
        ).strip()
        return ChatResponse(text=text)

    def stream_chat(
        self,
        messages: list[Message],
        *,
        system: str,
        images: list[bytes] | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Yield the reply as text deltas so callers can pipeline TTS.

        Optional capability: callers discover it via getattr(llm,
        "stream_chat", None) and fall back to chat() when absent, so other
        providers don't have to implement it.
        """
        with self._client.messages.stream(
            model=self._model,
            system=_system_blocks(system),
            messages=_build_anthropic_messages(messages, images),
            max_tokens=max_tokens or self._max_tokens,
        ) as stream:
            for delta in stream.text_stream:
                if delta:
                    yield delta
