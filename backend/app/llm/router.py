"""Provider router.

Resolves a provider adapter for a given user. Today only Claude exists;
Task 4 adds Gemini, OpenAI, and Bedrock entries here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.llm.base import LLMProvider
from app.llm.bedrock import BedrockProvider
from app.llm.claude import ClaudeProvider, ProviderUnavailableError
from app.llm.gemini import GeminiProvider
from app.llm.openai_provider import OpenAIProvider

# Each entry is a zero-arg factory so we can lazily instantiate providers
# (and surface a clear error when an API key is missing).
PROVIDER_FACTORIES: dict[str, Callable[[], LLMProvider]] = {
    "claude": ClaudeProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "bedrock": BedrockProvider,
}


class UnknownProviderError(ValueError):
    """Raised when the configured provider name is not registered."""


def get_provider(name: str) -> LLMProvider:
    factory = PROVIDER_FACTORIES.get(name)
    if factory is None:
        raise UnknownProviderError(
            f"Unknown LLM provider {name!r}. Known: {sorted(PROVIDER_FACTORIES)}"
        )
    return factory()


def get_provider_for_user(user: Mapping[str, object]) -> LLMProvider:
    name = str(user.get("llm_provider", "claude"))
    return get_provider(name)


__all__ = [
    "PROVIDER_FACTORIES",
    "ProviderUnavailableError",
    "UnknownProviderError",
    "get_provider",
    "get_provider_for_user",
]
