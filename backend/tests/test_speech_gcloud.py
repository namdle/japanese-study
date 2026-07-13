"""Unit tests for GCloudSpeechProvider with mocked Google clients."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.speech.base import TutorVoice
from app.speech.gcloud import GCloudSpeechProvider, split_by_language

# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #


def test_transcribe_concatenates_alternatives() -> None:
    stt = MagicMock()
    stt.recognize.return_value = SimpleNamespace(
        results=[
            SimpleNamespace(alternatives=[SimpleNamespace(transcript="こんにちは")]),
            SimpleNamespace(alternatives=[SimpleNamespace(transcript="元気ですか")]),
        ],
    )
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    text = provider.transcribe(b"audio-bytes")

    assert text == "こんにちは 元気ですか"
    stt.recognize.assert_called_once()
    kwargs = stt.recognize.call_args.kwargs
    assert kwargs["audio"].content == b"audio-bytes"
    assert kwargs["config"].language_code == "ja-JP"


def test_transcribe_returns_empty_when_no_results() -> None:
    stt = MagicMock()
    stt.recognize.return_value = SimpleNamespace(results=[])
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    assert provider.transcribe(b"x") == ""


def test_transcribe_passes_phrase_hints_as_boosted_context() -> None:
    stt = MagicMock()
    stt.recognize.return_value = SimpleNamespace(results=[])
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    provider.transcribe(b"x", phrase_hints=["はじめまして", "はじめまして"])

    config = stt.recognize.call_args.kwargs["config"]
    assert len(config.speech_contexts) == 1
    ctx = config.speech_contexts[0]
    assert list(ctx.phrases) == ["はじめまして"]  # de-duplicated
    assert ctx.boost > 0


def test_transcribe_name_gets_its_own_stronger_context() -> None:
    stt = MagicMock()
    stt.recognize.return_value = SimpleNamespace(results=[])
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    # The name (strong) must be boosted higher than the vocab so it wins
    # ambiguous cases, and must not be duplicated in the vocab context.
    provider.transcribe(b"x", strong_hints=["ナム"], phrase_hints=["はじめまして", "ナム"])

    contexts = stt.recognize.call_args.kwargs["config"].speech_contexts
    assert len(contexts) == 2
    strong, vocab = contexts[0], contexts[1]
    assert list(strong.phrases) == ["ナム"]
    assert list(vocab.phrases) == ["はじめまして"]  # ナム de-duplicated out of vocab
    assert strong.boost > vocab.boost


def test_transcribe_no_context_without_hints() -> None:
    stt = MagicMock()
    stt.recognize.return_value = SimpleNamespace(results=[])
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    provider.transcribe(b"x")

    assert list(stt.recognize.call_args.kwargs["config"].speech_contexts) == []


# --------------------------------------------------------------------------- #
# split_by_language
# --------------------------------------------------------------------------- #


def test_split_pure_japanese() -> None:
    segments = split_by_language("こんにちは、元気ですか?")
    assert len(segments) == 1
    assert segments[0].lang == "ja"


def test_split_pure_english() -> None:
    segments = split_by_language("Hello, how are you?")
    assert len(segments) == 1
    assert segments[0].lang == "en"


def test_split_mixed_japanese_then_english_in_parens() -> None:
    text = "コイさんはどこからきましたか?(Where are you from, Khoi?)"
    segments = split_by_language(text)
    langs = [s.lang for s in segments]
    assert langs == ["ja", "en"]
    # The Japanese run keeps its own punctuation; the English run keeps "(...)".
    assert "コイさん" in segments[0].text
    assert "Where are you from" in segments[1].text


def test_split_alternating() -> None:
    text = "Hello これは a test です end"
    segments = split_by_language(text)
    assert [s.lang for s in segments] == ["en", "ja", "en", "ja", "en"]


def test_split_empty_input_returns_empty_list() -> None:
    assert split_by_language("") == []
    assert split_by_language("   ") == []


# --------------------------------------------------------------------------- #
# TTS — single language
# --------------------------------------------------------------------------- #


def _set_tts_audio(tts: MagicMock, audio_bytes: bytes = b"mp3") -> None:
    tts.synthesize_speech.return_value = SimpleNamespace(audio_content=audio_bytes)


def test_synthesize_misa_japanese_uses_neural2_b() -> None:
    tts = MagicMock()
    _set_tts_audio(tts, b"jp_audio")
    provider = GCloudSpeechProvider(stt_client=MagicMock(), tts_client=tts)

    audio = provider.synthesize("ありがとう", voice=TutorVoice.MISA)

    assert audio.audio == b"jp_audio"
    assert audio.mime_type == "audio/mpeg"
    assert tts.synthesize_speech.call_count == 1
    kwargs = tts.synthesize_speech.call_args.kwargs
    assert kwargs["voice"].name == "ja-JP-Neural2-B"
    assert kwargs["voice"].ssml_gender.name == "FEMALE"
    assert kwargs["voice"].language_code == "ja-JP"
    assert kwargs["input"].text == "ありがとう"


def test_synthesize_hiro_japanese_uses_neural2_c() -> None:
    tts = MagicMock()
    _set_tts_audio(tts)
    provider = GCloudSpeechProvider(stt_client=MagicMock(), tts_client=tts)

    provider.synthesize("hi", voice=TutorVoice.HIRO)

    # "hi" is pure English, so we use the English Hiro voice.
    kwargs = tts.synthesize_speech.call_args.kwargs
    assert kwargs["voice"].name == "en-US-Neural2-D"
    assert kwargs["voice"].language_code == "en-US"


def test_synthesize_pure_english_uses_en_voice_for_misa() -> None:
    tts = MagicMock()
    _set_tts_audio(tts)
    provider = GCloudSpeechProvider(stt_client=MagicMock(), tts_client=tts)

    provider.synthesize("Hello there!", voice=TutorVoice.MISA)

    kwargs = tts.synthesize_speech.call_args.kwargs
    assert kwargs["voice"].name == "en-US-Neural2-F"
    assert kwargs["voice"].language_code == "en-US"


# --------------------------------------------------------------------------- #
# TTS — mixed language
# --------------------------------------------------------------------------- #


def test_synthesize_mixed_japanese_english_calls_both_voices_and_concats() -> None:
    tts = MagicMock()
    # Each call returns different bytes so we can verify concat ordering.
    tts.synthesize_speech.side_effect = [
        SimpleNamespace(audio_content=b"JP_AUDIO_"),
        SimpleNamespace(audio_content=b"EN_AUDIO"),
    ]
    provider = GCloudSpeechProvider(stt_client=MagicMock(), tts_client=tts)

    audio = provider.synthesize(
        "コイさんはどこからきましたか?(Where are you from, Khoi?)",
        voice=TutorVoice.MISA,
    )

    assert audio.audio == b"JP_AUDIO_EN_AUDIO"
    assert audio.mime_type == "audio/mpeg"
    assert tts.synthesize_speech.call_count == 2

    first_call_voice = tts.synthesize_speech.call_args_list[0].kwargs["voice"]
    second_call_voice = tts.synthesize_speech.call_args_list[1].kwargs["voice"]
    assert first_call_voice.name == "ja-JP-Neural2-B"
    assert first_call_voice.language_code == "ja-JP"
    assert second_call_voice.name == "en-US-Neural2-F"
    assert second_call_voice.language_code == "en-US"


def test_synthesize_audio_config_uses_consistent_sample_rate() -> None:
    tts = MagicMock()
    _set_tts_audio(tts)
    provider = GCloudSpeechProvider(stt_client=MagicMock(), tts_client=tts)
    provider.synthesize("こんにちは", voice=TutorVoice.MISA)
    kwargs = tts.synthesize_speech.call_args.kwargs
    assert kwargs["audio_config"].sample_rate_hertz == 24000


def test_tutor_voice_from_string_falls_back_to_misa() -> None:
    assert TutorVoice.from_string("Misa") == TutorVoice.MISA
    assert TutorVoice.from_string("Hiro") == TutorVoice.HIRO
    assert TutorVoice.from_string("Bogus") == TutorVoice.MISA


# --------------------------------------------------------------------------- #
# Streaming STT
# --------------------------------------------------------------------------- #


def _stream_response(*, transcript=None, is_final=False, event=0):
    results = []
    if transcript is not None:
        results = [
            SimpleNamespace(
                is_final=is_final,
                alternatives=[SimpleNamespace(transcript=transcript)],
            )
        ]
    return SimpleNamespace(speech_event_type=event, results=results)


def test_streaming_transcribe_streams_chunks_and_reports_events() -> None:
    from google.cloud import speech_v1

    end_event = (
        speech_v1.StreamingRecognizeResponse.SpeechEventType.END_OF_SINGLE_UTTERANCE
    )
    stt = MagicMock()
    sent_chunks: list[bytes] = []

    def fake_streaming_recognize(config, requests):
        for req in requests:
            sent_chunks.append(req.audio_content)
        yield _stream_response(transcript="こん", is_final=False)
        yield _stream_response(event=end_event)
        yield _stream_response(transcript="こんにちは。", is_final=True)

    stt.streaming_recognize.side_effect = fake_streaming_recognize
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    interims: list[str] = []
    endpoints: list[bool] = []
    text = provider.streaming_transcribe(
        iter([b"chunk1", b"chunk2", b""]),
        phrase_hints=["はじめまして"],
        strong_hints=["ナム"],
        on_interim=interims.append,
        on_endpoint=lambda: endpoints.append(True),
    )

    assert text == "こんにちは。"
    assert interims == ["こん"]
    assert endpoints == [True]
    assert sent_chunks == [b"chunk1", b"chunk2"]  # empty chunks skipped

    config = stt.streaming_recognize.call_args.args[0]
    assert config.single_utterance is True
    assert config.interim_results is True
    rec = config.config
    from google.cloud.speech_v1 import RecognitionConfig

    assert rec.encoding == RecognitionConfig.AudioEncoding.WEBM_OPUS
    assert rec.sample_rate_hertz == 48000
    # The name keeps its own max-boost context, vocab a moderate one.
    assert list(rec.speech_contexts[0].phrases) == ["ナム"]
    assert rec.speech_contexts[0].boost > rec.speech_contexts[1].boost


def test_streaming_transcribe_joins_multiple_finals() -> None:
    stt = MagicMock()
    stt.streaming_recognize.return_value = iter(
        [
            _stream_response(transcript="はい。", is_final=True),
            _stream_response(transcript="そうです。", is_final=True),
        ]
    )
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    text = provider.streaming_transcribe(iter([b"c"]))
    assert text == "はい。 そうです。"


def test_streaming_transcribe_auto_endpoint_off_disables_single_utterance() -> None:
    """Auto-stop off: Google must keep transcribing across pauses instead of
    finalizing at the first silence."""
    stt = MagicMock()
    stt.streaming_recognize.return_value = iter([])
    provider = GCloudSpeechProvider(stt_client=stt, tts_client=MagicMock())

    provider.streaming_transcribe(iter([b"c"]), auto_endpoint=False)

    config = stt.streaming_recognize.call_args.args[0]
    assert config.single_utterance is False
    assert config.interim_results is True
