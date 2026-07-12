"""Extract the learner-facing study sections from a lesson plan.

A lesson plan's markdown is authored for the tutor and contains sections the
learner shouldn't see mid-practice (the Example exchange would spoil it; the
Register/Tutor notes are meta-guidance). This pulls just the three sections a
learner benefits from previewing: Scenario, Target vocabulary, Key sentence
patterns — in that canonical order.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^##\s+(.*)$", re.M)

# Heading-prefix (lowercased) → keep. Order here is the display order.
_WANTED_PREFIXES = ("scenario", "target vocab", "key sentence")


def extract_study_sections(plan_markdown: str | None) -> str:
    """Return markdown with only Scenario / Target vocabulary / Key sentence
    patterns, in that order. Empty string if the plan has no such sections."""
    if not plan_markdown:
        return ""

    headings = list(_HEADING_RE.finditer(plan_markdown))
    if not headings:
        return ""

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(headings):
        heading = m.group(1).strip()
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(plan_markdown)
        body = plan_markdown[start:end].strip("\n")
        sections.append((heading, body))

    out: list[str] = []
    for prefix in _WANTED_PREFIXES:
        for heading, body in sections:
            if heading.lower().startswith(prefix):
                out.append(f"## {heading}\n{body}")
                break
    return "\n\n".join(out).strip()
