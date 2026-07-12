"""Google Gemini adapter for the LLMProvider interface.

Uses the google-genai SDK. Reads GOOGLE_API_KEY from the environment.
"""

from __future__ import annotations

import os

from google import genai
from google.genai import types as genai_types

from app.llm.base import ChatResponse, LLMProvider, Message
from app.llm.claude import ProviderUnavailableError
from app.session.uploads import detect_image_mime

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_MODEL, client=None):
        resolved_key = api_key or os.getenv("GOOGLE_API_KEY")
        if client is not None:
            self._client = client
        elif not resolved_key:
            raise ProviderUnavailableError(
                "Gemini provider is not configured. Set GOOGLE_API_KEY."
            )
        else:
            self._client = genai.Client(api_key=resolved_key)
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
        contents: list[genai_types.Content] = []
        for i, m in enumerate(messages):
            parts: list[genai_types.Part] = [genai_types.Part(text=m.content)]
            # Attach images to the LAST user message.
            is_last = i == len(messages) - 1
            if images and is_last and m.role == "user":
                for raw in images:
                    parts.insert(
                        0,
                        genai_types.Part.from_bytes(
                            data=raw,
                            mime_type=detect_image_mime(raw) or "image/jpeg",
                        ),
                    )
            contents.append(
                genai_types.Content(
                    role="user" if m.role == "user" else "model",
                    parts=parts,
                )
            )
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        text = response.text or ""
        return ChatResponse(text=text.strip())
