"""Unit tests for extract_study_sections."""

from __future__ import annotations

from app.curriculum.study import extract_study_sections

PLAN = """\
## Scenario
You arrive at your host family.

## Target vocabulary
- **こんにちは** — hello

## Key sentence patterns
- はじめまして。

## Register — who you're talking to
- polite vs casual

## Example exchange
「Tutor:」 ...

## Tutor notes
- expect mistakes
"""


def test_keeps_only_the_three_learner_sections() -> None:
    out = extract_study_sections(PLAN)
    assert "## Scenario" in out
    assert "## Target vocabulary" in out
    assert "## Key sentence patterns" in out


def test_excludes_tutor_only_sections() -> None:
    out = extract_study_sections(PLAN)
    assert "Register" not in out
    assert "Example exchange" not in out
    assert "Tutor notes" not in out
    assert "expect mistakes" not in out


def test_preserves_canonical_order() -> None:
    out = extract_study_sections(PLAN)
    assert out.index("Scenario") < out.index("Target vocabulary") < out.index("Key sentence")


def test_empty_for_none_or_unstructured() -> None:
    assert extract_study_sections(None) == ""
    assert extract_study_sections("just some freeform text, no headings") == ""
