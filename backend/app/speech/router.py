"""Speech provider router.

Today only Google Cloud is registered; Task 6 adds the OpenAI adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.speech.base import SpeechProvider, SpeechProviderUnavailableError
from app.speech.gcloud import GCloudSpeechProvider
from app.speech.openai_speech import OpenAISpeechProvider

PROVIDER_FACTORIES: dict[str, Callable[[], SpeechProvider]] = {
    "gcloud": GCloudSpeechProvider,
    "openai": OpenAISpeechProvider,
}


class UnknownSpeechProviderError(ValueError):
    pass


def get_speech_provider(name: str) -> SpeechProvider:
    factory = PROVIDER_FACTORIES.get(name)
    if factory is None:
        raise UnknownSpeechProviderError(
            f"Unknown speech provider {name!r}. Known: {sorted(PROVIDER_FACTORIES)}"
        )
    return factory()


def get_speech_provider_for_user(user: Mapping[str, object]) -> SpeechProvider:
    name = str(user.get("speech_provider", "gcloud"))
    return get_speech_provider(name)


__all__ = [
    "PROVIDER_FACTORIES",
    "SpeechProviderUnavailableError",
    "UnknownSpeechProviderError",
    "get_speech_provider",
    "get_speech_provider_for_user",
]
