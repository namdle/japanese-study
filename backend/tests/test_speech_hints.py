"""Unit tests for speech-adaptation phrase-hint extraction."""

from __future__ import annotations

from app.speech.hints import build_phrase_hints, extract_vocab_hints

PLAN = """\
## Scenario
Meeting the host family (玄関) for the first time.

## Target vocabulary
- **はじめまして** (hajimemashite) — Nice to meet you
- **名前** (namae) — name
- **～歳** (sai) — years old
- **アメリカ** (amerika) — America

## Key sentence patterns
- **はじめまして。私は ___ です。** — Nice to meet you. I'm ___.
"""


def test_extract_vocab_pulls_bold_japanese_terms_from_section() -> None:
    hints = extract_vocab_hints(PLAN)
    assert hints == ["はじめまして", "名前", "歳", "アメリカ"]


def test_extract_vocab_skips_fill_in_blank_patterns() -> None:
    # The "私は ___ です。" pattern in Key sentence patterns must not leak in.
    assert "私は ___ です。" not in extract_vocab_hints(PLAN)


def test_extract_vocab_empty_for_none_or_blank() -> None:
    assert extract_vocab_hints(None) == []
    assert extract_vocab_hints("") == []


def test_extract_vocab_falls_back_to_whole_doc_without_heading() -> None:
    assert extract_vocab_hints("- **元気** — fine") == ["元気"]


def test_build_phrase_hints_prepends_name_and_dedupes() -> None:
    hints = build_phrase_hints(name_ja="ナム", plan_markdown=PLAN)
    assert hints[0] == "ナム"
    assert "名前" in hints
    # No duplicates.
    assert len(hints) == len(set(hints))


def test_build_phrase_hints_ignores_blank_name() -> None:
    assert build_phrase_hints(name_ja="   ", plan_markdown=None) == []
