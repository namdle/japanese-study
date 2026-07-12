"""LLM-driven extraction of vocab/grammar/mistakes/topics from a session.

Flow:
1. Build a transcript (only user + assistant turns).
2. Send a strict-JSON extraction prompt to the LLM.
3. Parse the JSON robustly (strip code fences, locate the first object).
4. Upsert into vocab_items / grammar_points / mistakes / topic_interests.

Mastery rules (per item, per session):
- 'encountered'     -> existing.mastery unchanged; new rows start at mastery=1.
- 'used_correctly'  -> +1, capped at 5.
- 'made_mistake'    -> -1, floored at 0.

Idempotency: dedupe by UNIQUE(user_id, jp) for vocab,
UNIQUE(user_id, code) for grammar, and UNIQUE(user_id, keyword) for
topics. Mistakes are append-only.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from app.db import (
    grammar_points_table,
    mistakes_table,
    session_turns_table,
    topic_interests_table,
    vocab_items_table,
)
from app.llm.base import Message

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = (
    "You are a language-learning analysis assistant. Read the conversation "
    "transcript provided by the next user message and produce a single JSON "
    "object describing what the LEARNER (the 'user' role) encountered or "
    "produced. The JSON must follow this exact shape and contain no extra "
    "keys, no commentary, and no markdown fences:\n"
    "{\n"
    '  "vocab": [\n'
    '    {"jp": "ありがとう", "reading": "arigatou", "en": "thank you", '
    '"outcome": "encountered"}\n'
    "  ],\n"
    '  "grammar": [\n'
    '    {"code": "te-form-request", "example_jp": "食べてください", '
    '"notes": "polite request", "outcome": "used_correctly"}\n'
    "  ],\n"
    '  "mistakes": [\n'
    '    {"mistake_type": "particle", "original": "わたしは行く", '
    '"corrected": "わたしが行く", "note": "subject marker"}\n'
    "  ],\n"
    '  "topics": [\n'
    '    {"keyword": "family", "weight": 1}\n'
    "  ]\n"
    "}\n"
    "Rules:\n"
    "- 'outcome' must be one of: 'encountered', 'used_correctly', 'made_mistake'.\n"
    "- vocab: 5-15 substantive words/phrases. Skip particles and です/ます.\n"
    "- grammar: 0-5 distinct points actually used in the conversation.\n"
    "- mistakes: 0-10 actual learner mistakes, not stylistic preferences.\n"
    "- topics: 1-3 keywords describing the conversation topic, in English.\n"
    "- All Japanese fields MUST use real Japanese script (no romaji).\n"
    "- If the conversation is too short or empty, return empty arrays."
)


@dataclass(frozen=True)
class ExtractionResult:
    vocab: list[dict] = field(default_factory=list)
    grammar: list[dict] = field(default_factory=list)
    mistakes: list[dict] = field(default_factory=list)
    topics: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_object(raw: str) -> str | None:
    """Pull the first JSON object out of a possibly-wrapped LLM response."""
    if not raw:
        return None
    fence = _FENCE_RE.search(raw)
    if fence:
        return fence.group(1).strip()
    # Find the first { ... } that balances. Don't try to be too clever — most
    # models return a clean object when asked.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start : end + 1]


_VALID_OUTCOMES = {"encountered", "used_correctly", "made_mistake"}


def parse_extraction(raw: str) -> ExtractionResult:
    """Best-effort parse of an LLM extraction response into ExtractionResult."""
    blob = _extract_json_object(raw)
    if blob is None:
        return ExtractionResult()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        logger.warning("Profile extraction JSON parse failed: %s", exc)
        return ExtractionResult()
    if not isinstance(data, dict):
        return ExtractionResult()

    def _list(key: str) -> list[dict]:
        v = data.get(key)
        if not isinstance(v, list):
            return []
        return [item for item in v if isinstance(item, dict)]

    # Sanity-clamp outcome values.
    vocab = []
    for item in _list("vocab"):
        jp = str(item.get("jp", "")).strip()
        if not jp:
            continue
        outcome = str(item.get("outcome", "encountered"))
        if outcome not in _VALID_OUTCOMES:
            outcome = "encountered"
        vocab.append(
            {
                "jp": jp,
                "reading": (str(item.get("reading", "")) or "").strip() or None,
                "en": (str(item.get("en", "")) or "").strip() or None,
                "outcome": outcome,
            }
        )

    grammar = []
    for item in _list("grammar"):
        code = str(item.get("code", "")).strip()
        if not code:
            continue
        outcome = str(item.get("outcome", "encountered"))
        if outcome not in _VALID_OUTCOMES:
            outcome = "encountered"
        grammar.append(
            {
                "code": code,
                "example_jp": (str(item.get("example_jp", "")) or "").strip() or None,
                "notes": (str(item.get("notes", "")) or "").strip() or None,
                "outcome": outcome,
            }
        )

    mistakes = []
    for item in _list("mistakes"):
        original = str(item.get("original", "")).strip()
        corrected = str(item.get("corrected", "")).strip()
        if not original or not corrected:
            continue
        mistakes.append(
            {
                "mistake_type": (str(item.get("mistake_type", "")) or "").strip() or None,
                "original": original,
                "corrected": corrected,
                "note": (str(item.get("note", "")) or "").strip() or None,
            }
        )

    topics = []
    for item in _list("topics"):
        keyword = str(item.get("keyword", "")).strip()
        if not keyword:
            continue
        weight = item.get("weight", 1)
        try:
            weight_int = max(1, int(weight))
        except (TypeError, ValueError):
            weight_int = 1
        topics.append({"keyword": keyword.lower(), "weight": weight_int})

    return ExtractionResult(vocab=vocab, grammar=grammar, mistakes=mistakes, topics=topics)


# --------------------------------------------------------------------------- #
# Transcript builder
# --------------------------------------------------------------------------- #


def build_transcript(engine: Engine, session_id: int) -> str:
    """Concatenate all user/assistant turns into a labeled transcript string."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(session_turns_table.c.role, session_turns_table.c.text)
            .where(session_turns_table.c.session_id == session_id)
            .order_by(session_turns_table.c.id)
        ).all()
    lines: list[str] = []
    for role, text in rows:
        if role not in ("user", "assistant"):
            continue
        label = "Learner" if role == "user" else "Tutor"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Persistence with mastery
# --------------------------------------------------------------------------- #


def _adjust_mastery(current: int, outcome: str) -> int:
    if outcome == "used_correctly":
        return min(5, current + 1)
    if outcome == "made_mistake":
        return max(0, current - 1)
    return current  # encountered: unchanged


def persist_extraction(
    engine: Engine,
    user_id: int,
    session_id: int,
    extracted: ExtractionResult,
) -> dict[str, int]:
    """Upsert items from the extraction. Returns counts of inserted vs updated."""
    now = datetime.now(UTC).replace(tzinfo=None)
    counts = {
        "vocab_inserted": 0,
        "vocab_updated": 0,
        "grammar_inserted": 0,
        "grammar_updated": 0,
        "mistakes_inserted": 0,
        "topics_inserted": 0,
        "topics_updated": 0,
    }
    with engine.begin() as conn:
        # ---- vocab ----
        for v in extracted.vocab:
            existing = conn.execute(
                select(vocab_items_table)
                .where(vocab_items_table.c.user_id == user_id)
                .where(vocab_items_table.c.jp == v["jp"])
            ).mappings().one_or_none()
            if existing is None:
                conn.execute(
                    insert(vocab_items_table).values(
                        user_id=user_id,
                        jp=v["jp"],
                        reading=v.get("reading"),
                        en=v.get("en"),
                        mastery=1,
                        first_session_id=session_id,
                        last_seen_at=now,
                    )
                )
                counts["vocab_inserted"] += 1
            else:
                new_mastery = _adjust_mastery(int(existing["mastery"]), v["outcome"])
                conn.execute(
                    update(vocab_items_table)
                    .where(vocab_items_table.c.id == existing["id"])
                    .values(
                        mastery=new_mastery,
                        last_seen_at=now,
                        # Fill in missing reading/en if we now have them.
                        reading=existing["reading"] or v.get("reading"),
                        en=existing["en"] or v.get("en"),
                    )
                )
                counts["vocab_updated"] += 1

        # ---- grammar ----
        for g in extracted.grammar:
            existing = conn.execute(
                select(grammar_points_table)
                .where(grammar_points_table.c.user_id == user_id)
                .where(grammar_points_table.c.code == g["code"])
            ).mappings().one_or_none()
            if existing is None:
                conn.execute(
                    insert(grammar_points_table).values(
                        user_id=user_id,
                        code=g["code"],
                        example_jp=g.get("example_jp"),
                        notes=g.get("notes"),
                        mastery=1,
                        last_seen_at=now,
                    )
                )
                counts["grammar_inserted"] += 1
            else:
                new_mastery = _adjust_mastery(int(existing["mastery"]), g["outcome"])
                conn.execute(
                    update(grammar_points_table)
                    .where(grammar_points_table.c.id == existing["id"])
                    .values(
                        mastery=new_mastery,
                        last_seen_at=now,
                        example_jp=existing["example_jp"] or g.get("example_jp"),
                        notes=existing["notes"] or g.get("notes"),
                    )
                )
                counts["grammar_updated"] += 1

        # ---- mistakes (append-only) ----
        for m in extracted.mistakes:
            conn.execute(
                insert(mistakes_table).values(
                    user_id=user_id,
                    session_id=session_id,
                    mistake_type=m.get("mistake_type"),
                    original=m["original"],
                    corrected=m["corrected"],
                    note=m.get("note"),
                    created_at=now,
                )
            )
            counts["mistakes_inserted"] += 1

        # ---- topic interests (weight accumulates) ----
        for t in extracted.topics:
            existing = conn.execute(
                select(topic_interests_table)
                .where(topic_interests_table.c.user_id == user_id)
                .where(topic_interests_table.c.keyword == t["keyword"])
            ).mappings().one_or_none()
            if existing is None:
                conn.execute(
                    insert(topic_interests_table).values(
                        user_id=user_id,
                        keyword=t["keyword"],
                        weight=int(t["weight"]),
                        last_seen_at=now,
                    )
                )
                counts["topics_inserted"] += 1
            else:
                conn.execute(
                    update(topic_interests_table)
                    .where(topic_interests_table.c.id == existing["id"])
                    .values(
                        weight=int(existing["weight"]) + int(t["weight"]),
                        last_seen_at=now,
                    )
                )
                counts["topics_updated"] += 1
    return counts


# --------------------------------------------------------------------------- #
# Top-level entry: extract + persist
# --------------------------------------------------------------------------- #


def extract_and_persist(
    engine: Engine,
    llm: object,
    user: Mapping[str, object],
    session_id: int,
) -> dict[str, int] | None:
    """Run extraction on a session's transcript and persist the results.

    Returns the counts dict from persist_extraction, or None when the
    transcript is empty or extraction couldn't produce anything.
    """
    transcript = build_transcript(engine, session_id)
    if not transcript.strip():
        return None
    user_message = (
        f"Conversation transcript (learner is {user.get('name', 'the learner')}, "
        f"level {user.get('level', 'A1')}):\n\n{transcript}"
    )
    try:
        response = llm.chat(  # type: ignore[attr-defined]
            [Message(role="user", content=user_message)],
            system=EXTRACTION_SYSTEM_PROMPT,
            temperature=0.2,
            # A full extraction (up to 15 vocab + grammar + mistakes + topics)
            # is Japanese-heavy JSON that easily exceeds the 1024-token default
            # and gets truncated mid-object, which then fails to parse. Give it
            # comfortable headroom so the JSON always closes.
            max_tokens=4096,
        )
    except Exception as exc:
        logger.warning("Profile extraction LLM call failed: %s", exc)
        return None
    parsed = parse_extraction(response.text or "")
    if not (parsed.vocab or parsed.grammar or parsed.mistakes or parsed.topics):
        return None
    return persist_extraction(engine, int(user["id"]), session_id, parsed)
