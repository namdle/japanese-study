"""Common LLM provider types and the tutor system prompt.

All adapters implement the LLMProvider Protocol. Task 3 only ships the
Claude adapter; Task 4 adds Gemini, OpenAI, and Bedrock under the same
shape. Multimodal (images=...) is a parameter today even though the chat
endpoint won't pass it until Task 10.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ChatResponse:
    text: str


class LLMProvider(Protocol):
    """Provider adapter contract."""

    name: str

    def chat(
        self,
        messages: list[Message],
        *,
        system: str,
        images: list[bytes] | None = None,
        temperature: float = 0.6,
    ) -> ChatResponse: ...


# --------------------------------------------------------------------------- #
# Tutor system prompt
# --------------------------------------------------------------------------- #

VOICE_GENDER = {"Misa": "female", "Hiro": "male"}


def build_tutor_system_prompt(
    user: Mapping[str, object],
    *,
    lesson_title: str | None = None,
    lesson_can_dos: list[str] | None = None,
    lesson_plan_markdown: str | None = None,
    mode: str = "freeform",
    profile_snapshot: str | None = None,
) -> str:
    """Compose the tutor persona and instructions for a given profile.

    Parameters:
      user: row mapping from the users table (voice, level, name, etc.)
      lesson_title: human-readable lesson title, e.g. "Saying hi"
      lesson_can_dos: bullet list of can-do statements the lesson targets
      lesson_plan_markdown: admin-approved markdown guidance for the LLM
      mode: 'freeform' or 'three_phase' (Task 9 enforces phase markers)
      profile_snapshot: pre-formatted text block of the learner's known
          vocab, weak grammar, recent mistakes, and topic interests.
    """
    voice = str(user.get("voice", "Misa"))
    gender = VOICE_GENDER.get(voice, "female")
    level = str(user.get("level", "A1"))
    name = str(user.get("name", "the learner"))
    explanation_language = str(user.get("explanation_language", "en"))
    correction_style = str(user.get("correction_style", "end_of_turn"))

    if explanation_language == "ja":
        explanation_clause = (
            "Stay in Japanese throughout. If the learner is stuck, paraphrase or "
            "give simple Japanese hints rather than switching to English."
        )
    else:
        explanation_clause = (
            "Reply in Japanese. Do NOT translate your Japanese into English; the "
            "learner can ask for a translation if they need one. Use English "
            "sparingly and only when a brief clarification or correction "
            "genuinely helps. By default, stay in Japanese."
        )

    if correction_style == "end_of_session":
        correction_clause = (
            "Do NOT interrupt to correct mistakes during the conversation. Stay "
            "engaged and conversational. Mistakes will be summarized for the "
            "learner at the end of the session."
        )
    else:  # end_of_turn (default)
        correction_clause = (
            "Only correct the learner when they make a clear mistake — not "
            "every turn. When you do correct, keep it brief (one short line) "
            "and encouraging, e.g. 'ちなみに、〇〇とも言えますよ。'. Do not "
            "translate the correction into English unless it is genuinely "
            "necessary for understanding."
        )

    lines: list[str] = [
        f"You are {voice}, a friendly Japanese tutor ({gender}).",
        f"You are practicing conversation with a learner named {name}.",
        f"Speak naturally in Japanese at CEFR level {level}.",
        "Avoid corporate or business contexts; favor topics like family, friends, "
        "school, hobbies, food, and daily life.",
        "Keep your replies short — about one to three sentences — so the learner "
        "has room to respond.",
        explanation_clause,
        correction_clause,
        f"Always introduce yourself as {voice} when greeting for the first time.",
    ]

    if lesson_title or lesson_can_dos or lesson_plan_markdown:
        lines.append("")
        lines.append("Today's lesson:")
        if lesson_title:
            lines.append(f"- Title: {lesson_title}")
        if lesson_can_dos:
            lines.append("- The learner is practicing these can-do goals:")
            for cd in lesson_can_dos:
                lines.append(f"  • {cd}")
        if lesson_plan_markdown and lesson_plan_markdown.strip():
            lines.append("")
            lines.append("Plan from the parent (use this as your guide):")
            lines.append(lesson_plan_markdown.strip())

    if mode == "three_phase":
        lines.append("")
        lines.append(
            "Run the session in three phases: warm-up (greet and check in), "
            "main practice (work on the can-do goals), and wrap-up "
            "(summarise what went well). Briefly mark each transition."
        )

    if profile_snapshot and profile_snapshot.strip():
        lines.append("")
        lines.append("Learner profile (use naturally, don't list it back):")
        lines.append(profile_snapshot.strip())

    show_hiragana = bool(user.get("show_hiragana", False))
    show_english = bool(user.get("show_english", False))
    if show_hiragana or show_english:
        lines.append("")
        lines.append(
            "Reading aids: after your Japanese reply, append the requested "
            "helper line(s) on their own lines, exactly as shown:"
        )
        if show_hiragana:
            lines.append(
                "[HIRAGANA] <your Japanese reply rewritten with all kanji "
                "converted to hiragana; keep punctuation>"
            )
        if show_english:
            lines.append(
                "[EN] <a brief, natural English translation of your Japanese "
                "reply>"
            )
        lines.append(
            "Append these on EVERY reply without exception — including short "
            "replies, corrections, and role-play/quoted lines. Do NOT include "
            "the helper lines inside the Japanese reply itself, and do NOT add "
            "any other markers or commentary."
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Parsing tutor replies that may include reading-aid lines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedReply:
    """Tutor reply split into Japanese + optional reading aids."""

    text: str
    hiragana: str | None
    english: str | None


_HIRAGANA_PREFIXES = ("[HIRAGANA]", "【HIRAGANA】", "[ひらがな]", "【ひらがな】")
_ENGLISH_PREFIXES = ("[EN]", "[ENGLISH]", "【EN】", "【ENGLISH】")


def parse_tutor_reply(raw: str) -> ParsedReply:
    """Split out [HIRAGANA] and [EN] helper lines from a tutor reply.

    Anything before the first helper line is treated as the Japanese reply.
    Helper lines that span multiple physical lines are joined back together
    until the next helper marker (or end of string).
    """
    if not raw:
        return ParsedReply(text="", hiragana=None, english=None)

    lines = raw.splitlines()
    ja_lines: list[str] = []
    hiragana_lines: list[str] = []
    english_lines: list[str] = []
    bucket: list[str] = ja_lines

    def matches_prefix(s: str, prefixes: tuple[str, ...]) -> str | None:
        for p in prefixes:
            if s.startswith(p):
                return s[len(p) :].lstrip(": ").strip()
        return None

    for line in lines:
        stripped = line.strip()
        ja_match = matches_prefix(stripped, ("[JA]", "【JA】"))
        if ja_match is not None:
            bucket = ja_lines
            if ja_match:
                ja_lines.append(ja_match)
            continue
        hir = matches_prefix(stripped, _HIRAGANA_PREFIXES)
        if hir is not None:
            bucket = hiragana_lines
            if hir:
                hiragana_lines.append(hir)
            continue
        en = matches_prefix(stripped, _ENGLISH_PREFIXES)
        if en is not None:
            bucket = english_lines
            if en:
                english_lines.append(en)
            continue
        bucket.append(line)

    text = "\n".join(ja_lines).strip()
    hiragana = "\n".join(hiragana_lines).strip() or None
    english = "\n".join(english_lines).strip() or None
    return ParsedReply(text=text, hiragana=hiragana, english=english)
