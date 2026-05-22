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
