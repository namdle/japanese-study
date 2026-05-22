"""Unit tests for ClaudeProvider.

We mock the anthropic.Anthropic client so no real API calls are made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.llm.base import Message, build_tutor_system_prompt
from app.llm.claude import ClaudeProvider, ProviderUnavailableError


def _fake_sdk_response(text: str) -> SimpleNamespace:
    """Mimic anthropic's response shape: .content is a list of TextBlocks."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_claude_chat_calls_sdk_with_expected_args() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = _fake_sdk_response("こんにちは!")
    provider = ClaudeProvider(client=sdk)

    response = provider.chat(
        [Message(role="user", content="やあ")],
        system="You are Misa",
        temperature=0.5,
    )

    assert response.text == "こんにちは!"
    sdk.messages.create.assert_called_once()
    kwargs = sdk.messages.create.call_args.kwargs
    assert kwargs["system"] == "You are Misa"
    assert kwargs["messages"] == [{"role": "user", "content": "やあ"}]
    assert kwargs["temperature"] == 0.5
    assert kwargs["model"] == "claude-sonnet-4-20250514"
    assert kwargs["max_tokens"] == 1024


def test_claude_chat_concatenates_multi_block_text() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="Hello "), SimpleNamespace(text="world")]
    )
    provider = ClaudeProvider(client=sdk)

    response = provider.chat(
        [Message(role="user", content="hi")], system="sys"
    )
    assert response.text == "Hello world"


def test_claude_chat_strips_whitespace() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = _fake_sdk_response("  hi  \n")
    provider = ClaudeProvider(client=sdk)

    response = provider.chat([Message(role="user", content="x")], system="sys")
    assert response.text == "hi"


def test_provider_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError):
        ClaudeProvider()


def test_build_tutor_prompt_uses_voice_and_level() -> None:
    user = {
        "name": "Sora",
        "voice": "Hiro",
        "level": "A2",
        "explanation_language": "en",
    }
    prompt = build_tutor_system_prompt(user)
    assert "Hiro" in prompt
    assert "Sora" in prompt
    assert "A2" in prompt
    assert "male" in prompt.lower()
    assert "english" in prompt.lower()


def test_build_tutor_prompt_immersion_mode() -> None:
    user = {
        "name": "Mom",
        "voice": "Misa",
        "level": "B1",
        "explanation_language": "ja",
    }
    prompt = build_tutor_system_prompt(user)
    assert "Misa" in prompt
    assert "Stay in Japanese" in prompt


def test_build_tutor_prompt_includes_lesson_when_provided() -> None:
    user = {"name": "Sora", "voice": "Misa", "level": "A1", "explanation_language": "en"}
    prompt = build_tutor_system_prompt(
        user,
        lesson_title="Saying hi",
        lesson_can_dos=["Greet someone", "Ask their name"],
        lesson_plan_markdown="# Plan\nGreet warmly.",
    )
    assert "Saying hi" in prompt
    assert "Greet someone" in prompt
    assert "Ask their name" in prompt
    assert "Greet warmly" in prompt
    assert "Plan from the parent" in prompt


def test_build_tutor_prompt_three_phase_mode() -> None:
    user = {"name": "Sora", "voice": "Misa", "level": "A1", "explanation_language": "en"}
    prompt = build_tutor_system_prompt(user, mode="three_phase")
    assert "three phases" in prompt.lower()
    assert "warm-up" in prompt.lower()
    assert "wrap-up" in prompt.lower()


def test_build_tutor_prompt_end_of_turn_correction() -> None:
    user = {
        "name": "Sora",
        "voice": "Misa",
        "level": "A1",
        "explanation_language": "en",
        "correction_style": "end_of_turn",
    }
    prompt = build_tutor_system_prompt(user)
    # The end-of-turn clause limits corrections to clear mistakes only.
    assert "Only correct" in prompt
    assert "not\nevery turn" in prompt or "not every turn" in prompt
    assert "summarized" not in prompt.lower()


def test_build_tutor_prompt_end_of_session_correction() -> None:
    user = {
        "name": "Sora",
        "voice": "Misa",
        "level": "A1",
        "explanation_language": "en",
        "correction_style": "end_of_session",
    }
    prompt = build_tutor_system_prompt(user)
    assert "Do NOT interrupt" in prompt
    assert "summarized" in prompt.lower()
    assert "Only correct" not in prompt


def test_build_tutor_prompt_discourages_routine_english_translation() -> None:
    user = {
        "name": "Sora",
        "voice": "Misa",
        "level": "A1",
        "explanation_language": "en",
    }
    prompt = build_tutor_system_prompt(user)
    # English-explanation mode should clearly tell the model NOT to translate
    # every Japanese line into English.
    assert "Do NOT translate" in prompt
    assert "stay in japanese" in prompt.lower()
    assert "By default" in prompt


def test_build_tutor_prompt_includes_hiragana_and_english_aids_when_enabled() -> None:
    user = {
        "name": "Sora",
        "voice": "Misa",
        "level": "A1",
        "explanation_language": "en",
        "show_hiragana": True,
        "show_english": True,
    }
    prompt = build_tutor_system_prompt(user)
    assert "[HIRAGANA]" in prompt
    assert "[EN]" in prompt
    assert "Reading aids" in prompt


def test_build_tutor_prompt_omits_aid_lines_when_disabled() -> None:
    user = {
        "name": "Sora",
        "voice": "Misa",
        "level": "A1",
        "explanation_language": "en",
        "show_hiragana": False,
        "show_english": False,
    }
    prompt = build_tutor_system_prompt(user)
    assert "[HIRAGANA]" not in prompt
    assert "Reading aids" not in prompt


def test_build_tutor_prompt_only_hiragana() -> None:
    user = {
        "name": "Sora",
        "voice": "Misa",
        "level": "A1",
        "show_hiragana": True,
    }
    prompt = build_tutor_system_prompt(user)
    assert "[HIRAGANA]" in prompt
    assert "[EN]" not in prompt


def test_build_tutor_prompt_includes_profile_snapshot() -> None:
    user = {"name": "Sora", "voice": "Misa", "level": "A1", "explanation_language": "en"}
    snapshot = "Known vocab: 犬(dog), 猫(cat)\nWeakest grammar: te-form(mastery 1)"
    prompt = build_tutor_system_prompt(user, profile_snapshot=snapshot)
    assert "Known vocab: 犬(dog)" in prompt
    assert "te-form(mastery 1)" in prompt
    assert "Learner profile" in prompt


def test_build_tutor_prompt_omits_profile_when_none() -> None:
    user = {"name": "Sora", "voice": "Misa", "level": "A1", "explanation_language": "en"}
    prompt = build_tutor_system_prompt(user, profile_snapshot=None)
    assert "Learner profile" not in prompt


def test_parse_tutor_reply_plain_japanese() -> None:
    from app.llm.base import parse_tutor_reply

    parsed = parse_tutor_reply("こんにちは、元気ですか?")
    assert parsed.text == "こんにちは、元気ですか?"
    assert parsed.hiragana is None
    assert parsed.english is None


def test_parse_tutor_reply_with_hiragana_and_english() -> None:
    from app.llm.base import parse_tutor_reply

    raw = (
        "こんにちは、元気ですか?\n"
        "[HIRAGANA] こんにちは、げんきですか?\n"
        "[EN] Hello, how are you?"
    )
    parsed = parse_tutor_reply(raw)
    assert parsed.text == "こんにちは、元気ですか?"
    assert parsed.hiragana == "こんにちは、げんきですか?"
    assert parsed.english == "Hello, how are you?"


def test_parse_tutor_reply_handles_inline_marker() -> None:
    """The model sometimes puts the marker on its own line with the value beneath."""
    from app.llm.base import parse_tutor_reply

    raw = "やあ!\n[EN]\nHi!\n[HIRAGANA]\nやあ!"
    parsed = parse_tutor_reply(raw)
    assert parsed.text == "やあ!"
    assert parsed.english == "Hi!"
    assert parsed.hiragana == "やあ!"


def test_parse_tutor_reply_only_japanese_when_no_markers() -> None:
    from app.llm.base import parse_tutor_reply

    parsed = parse_tutor_reply("いただきます")
    assert parsed.text == "いただきます"
    assert parsed.hiragana is None
    assert parsed.english is None
