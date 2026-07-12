"""OpenAI (ChatGPT) adapter for the LLMProvider interface.

Uses the openai SDK. Reads OPENAI_API_KEY from the environment.
"""

from __future__ import annotations

import base64
import os

import openai

from app.llm.base import ChatResponse, LLMProvider, Message
from app.llm.claude import ProviderUnavailableError
from app.session.uploads import detect_image_mime

DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_MODEL, client=None):
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.getenv("OPENAI_API_KEY")
            if not resolved_key:
                raise ProviderUnavailableError(
                    "OpenAI provider is not configured. Set OPENAI_API_KEY."
                )
            self._client = openai.OpenAI(api_key=resolved_key)
        self._model = model

    def chat(
        self,
        messages: list[Message],
        *,
        system: str,
        images: list[bytes] | None = None,
        temperature: float = 0.6,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        oai_messages: list[dict] = [{"role": "system", "content": system}]
        for i, m in enumerate(messages):
            is_last = i == len(messages) - 1
            if images and is_last and m.role == "user":
                content_parts: list[dict] = [
                    {"type": "text", "text": m.content},
                ]
                for raw in images:
                    mime = detect_image_mime(raw) or "image/jpeg"
                    b64 = base64.b64encode(raw).decode("ascii")
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        }
                    )
                oai_messages.append({"role": "user", "content": content_parts})
            else:
                oai_messages.append({"role": m.role, "content": m.content})

        extra: dict = {}
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens
        response = self._client.chat.completions.create(
            model=self._model,
            messages=oai_messages,
            temperature=temperature,
            **extra,
        )
        text = response.choices[0].message.content or ""
        return ChatResponse(text=text.strip())
