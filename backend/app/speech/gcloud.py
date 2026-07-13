"""Google Cloud Speech-to-Text + Text-to-Speech adapter.

Uses google-cloud-speech and google-cloud-texttospeech. Authentication
follows the standard Google credential chain — typically
GOOGLE_APPLICATION_CREDENTIALS pointing at a service-account JSON file.

Voice mapping (high-naturalness Neural2 voices):

  Japanese (ja-JP):
    Misa  -> ja-JP-Neural2-B (female)
    Hiro  -> ja-JP-Neural2-C (male)

  English (en-US): used automatically for English fragments inside an
  otherwise-Japanese reply (e.g. parenthetical translations) so the
  English isn't pronounced with a heavy Japanese accent.
    Misa  -> en-US-Neural2-F (female)
    Hiro  -> en-US-Neural2-D (male)

When the LLM mixes Japanese and English in the same reply, we split the
text into language runs, synthesize each run with the appropriate voice,
and concatenate the resulting MP3 bytes. MP3 frames are self-contained, so
naive byte concatenation plays cleanly in browsers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from typing import NamedTuple

from app.speech.base import (
    SpeechProvider,
    SpeechProviderUnavailableError,
    SynthesizedAudio,
    TutorVoice,
)

# Misa/Hiro voices for each language, plus the SSML gender we declare.
JA_VOICE_MAP: dict[TutorVoice, tuple[str, str]] = {
    TutorVoice.MISA: ("ja-JP-Neural2-B", "FEMALE"),
    TutorVoice.HIRO: ("ja-JP-Neural2-C", "MALE"),
}
EN_VOICE_MAP: dict[TutorVoice, tuple[str, str]] = {
    TutorVoice.MISA: ("en-US-Neural2-F", "FEMALE"),
    TutorVoice.HIRO: ("en-US-Neural2-D", "MALE"),
}

# Match *script* chars: Hiragana, Katakana, CJK Unified Ideographs.
# Punctuation, digits, and whitespace are language-neutral and attach to
# whichever segment they sit between.
_JA_SCRIPT_RE = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")


class _LangSegment(NamedTuple):
    lang: str  # "ja" or "en"
    text: str


def split_by_language(text: str) -> list[_LangSegment]:
    """Split text into alternating runs of Japanese and English script.

    Whitespace and punctuation are not language-classified themselves; they
    extend the run that came before them. A leading run of punctuation is
    attached to whichever language appears first.
    """
    if not text.strip():
        return []

    segments: list[_LangSegment] = []
    current_lang: str | None = None
    chunks: list[str] = []

    def lang_of(ch: str) -> str | None:
        if _JA_SCRIPT_RE.match(ch):
            return "ja"
        if _LATIN_LETTER_RE.match(ch):
            return "en"
        return None  # neutral

    for ch in text:
        ch_lang = lang_of(ch)
        if ch_lang is not None and current_lang is not None and ch_lang != current_lang:
            segments.append(_LangSegment(current_lang, "".join(chunks)))
            chunks = []
        if ch_lang is not None:
            current_lang = ch_lang
        chunks.append(ch)

    if chunks:
        # If we never saw any language-bearing char, default to Japanese.
        segments.append(_LangSegment(current_lang or "ja", "".join(chunks)))

    return segments


class GCloudSpeechProvider(SpeechProvider):
    name = "gcloud"

    def __init__(self, *, stt_client=None, tts_client=None) -> None:
        # Lazy imports so tests with injected clients don't require credentials.
        if stt_client is not None and tts_client is not None:
            self._stt = stt_client
            self._tts = tts_client
            return
        try:
            from google.cloud import speech_v1, texttospeech
        except ImportError as exc:  # pragma: no cover - defensive
            raise SpeechProviderUnavailableError(
                "google-cloud-speech / google-cloud-texttospeech are not installed"
            ) from exc

        try:
            self._stt = stt_client or speech_v1.SpeechClient()
            self._tts = tts_client or texttospeech.TextToSpeechClient()
        except Exception as exc:
            raise SpeechProviderUnavailableError(
                "Google Cloud speech credentials are not configured. "
                "Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON path."
            ) from exc

    # ------------------------------------------------------------------ #
    # STT
    # ------------------------------------------------------------------ #
    def transcribe(
        self,
        audio: bytes,
        *,
        language: str = "ja-JP",
        phrase_hints: list[str] | None = None,
        strong_hints: list[str] | None = None,
    ) -> str:
        from google.cloud import speech_v1

        # Speech adaptation: bias recognition toward expected words (e.g.
        # ナム vs ラム/ナムル). Without this, near-homophones win.
        # Let Google auto-detect the encoding by leaving `encoding` unset.
        config = speech_v1.RecognitionConfig(
            language_code=language,
            enable_automatic_punctuation=True,
            speech_contexts=self._speech_contexts(phrase_hints, strong_hints),
        )
        recognition_audio = speech_v1.RecognitionAudio(content=audio)
        response = self._stt.recognize(config=config, audio=recognition_audio)
        parts: list[str] = []
        for result in response.results:
            if result.alternatives:
                parts.append(result.alternatives[0].transcript)
        return " ".join(p.strip() for p in parts).strip()

    def _speech_contexts(
        self, phrase_hints: list[str] | None, strong_hints: list[str] | None
    ) -> list:
        """Speech-adaptation contexts shared by batch and streaming STT.

        The learner's name gets its own maximum-boost context so it wins
        ambiguous cases; the lesson vocabulary is a moderate boost.
        """
        from google.cloud import speech_v1

        speech_contexts = []
        strong = list(dict.fromkeys(h for h in (strong_hints or []) if h and h.strip()))
        if strong:
            speech_contexts.append(speech_v1.SpeechContext(phrases=strong, boost=20.0))
        strong_set = set(strong)
        vocab = list(
            dict.fromkeys(
                h for h in (phrase_hints or []) if h and h.strip() and h not in strong_set
            )
        )
        if vocab:
            speech_contexts.append(speech_v1.SpeechContext(phrases=vocab, boost=15.0))
        return speech_contexts

    def streaming_transcribe(
        self,
        chunks: Iterable[bytes],
        *,
        language: str = "ja-JP",
        phrase_hints: list[str] | None = None,
        strong_hints: list[str] | None = None,
        auto_endpoint: bool = True,
        on_interim: Callable[[str], None] | None = None,
        on_endpoint: Callable[[], None] | None = None,
    ) -> str:
        """Streaming STT with server-side endpointing (optional capability).

        `chunks` is a live stream of WEBM/Opus slices from the browser's
        MediaRecorder (first chunk carries the container header; later ones
        are continuations — exactly what Google's WEBM_OPUS streaming mode
        expects).

        With `auto_endpoint=True` (`single_utterance` mode), Google detects
        the end of speech itself and finalizes, so the transcript is ready
        the moment the learner stops talking — no client-side silence
        window. With `auto_endpoint=False` — the learner turned auto-stop
        off — Google keeps transcribing across pauses (multiple finals are
        joined) until the chunk stream ends at manual stop.

        Callbacks (invoked from the calling thread while responses stream):
          on_interim(text): partial hypothesis, for live captions.
          on_endpoint(): Google detected end of utterance — the caller
              should tell the client to stop the mic. Never fires when
              auto_endpoint is False.
        """
        from google.cloud import speech_v1

        streaming_config = speech_v1.StreamingRecognitionConfig(
            config=speech_v1.RecognitionConfig(
                encoding=speech_v1.RecognitionConfig.AudioEncoding.WEBM_OPUS,
                sample_rate_hertz=48000,  # browsers record Opus at 48 kHz
                language_code=language,
                enable_automatic_punctuation=True,
                speech_contexts=self._speech_contexts(phrase_hints, strong_hints),
            ),
            interim_results=True,
            single_utterance=auto_endpoint,
        )

        def requests() -> Iterator:
            for chunk in chunks:
                if chunk:
                    yield speech_v1.StreamingRecognizeRequest(audio_content=chunk)

        end_of_utterance = (
            speech_v1.StreamingRecognizeResponse.SpeechEventType.END_OF_SINGLE_UTTERANCE
        )
        finals: list[str] = []
        for response in self._stt.streaming_recognize(streaming_config, requests()):
            if response.speech_event_type == end_of_utterance and on_endpoint is not None:
                on_endpoint()
            for result in response.results:
                if not result.alternatives:
                    continue
                text = result.alternatives[0].transcript
                if result.is_final:
                    finals.append(text)
                elif on_interim is not None:
                    on_interim(text)
        return " ".join(p.strip() for p in finals).strip()

    # ------------------------------------------------------------------ #
    # TTS
    # ------------------------------------------------------------------ #
    def synthesize(
        self,
        text: str,
        *,
        voice: TutorVoice,
        language: str = "ja-JP",  # noqa: ARG002 - kept for SpeechProvider compat
    ) -> SynthesizedAudio:
        segments = split_by_language(text)
        if not segments:
            return SynthesizedAudio(audio=b"", mime_type="audio/mpeg")

        # Fast path: a single segment in either language.
        if len(segments) == 1:
            seg = segments[0]
            return self._synthesize_segment(seg.text, voice=voice, lang=seg.lang)

        # Mixed: synthesize each run with the matching voice and concat MP3 bytes.
        audio_parts: list[bytes] = []
        for seg in segments:
            if not seg.text.strip():
                continue
            audio_parts.append(
                self._synthesize_segment(seg.text, voice=voice, lang=seg.lang).audio
            )
        return SynthesizedAudio(audio=b"".join(audio_parts), mime_type="audio/mpeg")

    def _synthesize_segment(
        self, text: str, *, voice: TutorVoice, lang: str
    ) -> SynthesizedAudio:
        from google.cloud import texttospeech

        if lang == "en":
            voice_name, gender_str = EN_VOICE_MAP[voice]
            language_code = "en-US"
        else:
            voice_name, gender_str = JA_VOICE_MAP[voice]
            language_code = "ja-JP"

        gender = getattr(texttospeech.SsmlVoiceGender, gender_str)
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name,
            ssml_gender=gender,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.0,
            sample_rate_hertz=24000,  # consistent across segments for clean concat
        )
        response = self._tts.synthesize_speech(
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
        )
        return SynthesizedAudio(audio=response.audio_content, mime_type="audio/mpeg")
