"""Build speech-adaptation phrase hints from lesson content.

Google STT / Whisper both let us bias transcription toward expected words.
For a curriculum-driven tutor the best hints are the exact words the learner
is practicing — so we pull the bolded terms out of a lesson plan's
"Target vocabulary" section — plus the learner's name in Japanese.
"""

from __future__ import annotations

import re

# Hiragana, Katakana, CJK ideographs — used to keep only Japanese-script hints.
_JA_SCRIPT_RE = re.compile(r"[぀-ヿ一-鿿]")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_VOCAB_SECTION_RE = re.compile(
    r"##\s*Target vocabulary\s*(.*?)(?:\n##\s|\Z)", re.S | re.I
)


def extract_vocab_hints(plan_markdown: str | None) -> list[str]:
    """Return the Japanese vocabulary terms from a lesson plan's markdown.

    Looks only inside the "## Target vocabulary" section (falling back to the
    whole document if that heading is absent) and returns the **bolded** terms
    that contain Japanese script, de-duplicated and order-preserving.
    """
    if not plan_markdown:
        return []
    section_match = _VOCAB_SECTION_RE.search(plan_markdown)
    section = section_match.group(1) if section_match else plan_markdown

    hints: list[str] = []
    for token in _BOLD_RE.findall(section):
        term = token.strip().strip("～").strip()
        # Drop fill-in-the-blank patterns and anything without Japanese script.
        if "_" in term or not _JA_SCRIPT_RE.search(term):
            continue
        hints.append(term)
    return list(dict.fromkeys(hints))


def build_phrase_hints(
    *, name_ja: str | None = None, plan_markdown: str | None = None
) -> list[str]:
    """Assemble the full phrase-hint list for a transcription request."""
    hints: list[str] = []
    if name_ja and name_ja.strip():
        hints.append(name_ja.strip())
    hints.extend(extract_vocab_hints(plan_markdown))
    return list(dict.fromkeys(hints))
