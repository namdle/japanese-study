"""Persisted sessions and per-session conversation turns.

A session is created by POST /api/sessions/start, which:
  - Picks the next approved lesson (or uses an explicit lesson_id).
  - Creates a sessions row capturing the user's preferences at start time.
  - Generates an opening greeting from the LLM and saves it as the first
    assistant turn so the learner sees something immediately.

Subsequent text and voice turns are persisted in session_turns. Navigating
away and back resumes the active session via GET /api/sessions/active.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from app.api.chat import get_provider_for_user_dep
from app.api.voice import get_speech_provider_dep
from app.config import get_settings
from app.curriculum.study import extract_study_sections
from app.db import (
    lesson_plans_table,
    lessons_table,
    session_turns_table,
    sessions_table,
    topics_table,
)
from app.deps import CurrentUser, EngineDep
from app.llm.base import (
    Message,
    ParsedReply,
    build_tutor_system_prompt,
    parse_tutor_reply,
)
from app.session.orchestrator import (
    NextLesson,
    get_lesson_for_session,
    pick_next_lesson,
)
from app.session.streaming import SentenceStreamer
from app.session.uploads import detect_image_mime, save_upload
from app.speech.base import SpeechProvider, TutorVoice
from app.speech.hints import extract_vocab_hints

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


SessionMode = Literal["freeform", "three_phase"]


class TurnOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    audio_url: str | None
    hiragana: str | None = None
    english: str | None = None
    created_at: datetime


class SessionOut(BaseModel):
    id: int
    user_id: int
    lesson_id: int | None
    lesson_plan_id: int | None
    mode: SessionMode
    tutor_voice: str
    llm_provider: str
    speech_provider: str
    started_at: datetime
    ended_at: datetime | None
    summary: str | None
    seed_image_url: str | None = None


class LessonInfoOut(BaseModel):
    id: int
    title_en: str
    title_ja: str
    level: str
    can_dos: list[str]
    topic_title_en: str
    topic_title_ja: str


class LessonOptionOut(LessonInfoOut):
    """An approved lesson the learner can pick, with their practice history."""

    practiced_count: int
    last_practiced_at: datetime | None


class LessonStudyOut(BaseModel):
    """Learner-facing study sections of a lesson (Scenario / vocab / patterns)."""

    lesson_id: int
    study_markdown: str


class SessionDetailOut(BaseModel):
    session: SessionOut
    lesson: LessonInfoOut | None
    turns: list[TurnOut]


class StartSessionBody(BaseModel):
    lesson_id: int | None = None
    mode: SessionMode = "freeform"


class TextTurnBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class ActiveSessionOut(BaseModel):
    active: SessionDetailOut | None
    next_lesson: LessonInfoOut | None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _row_to_session(row: Mapping[str, object]) -> SessionOut:
    data = dict(row)
    seed_image_path = data.pop("seed_image_path", None)
    if seed_image_path:
        # The stored path looks like "uploads/<user_id>/<file>".
        data["seed_image_url"] = f"/api/{seed_image_path}"
    else:
        data["seed_image_url"] = None
    return SessionOut.model_validate(data)


def _row_to_turn(row: Mapping[str, object]) -> TurnOut:
    audio_path = row.get("audio_path")
    audio_url = f"/api/audio/{audio_path}" if audio_path else None
    return TurnOut(
        id=int(row["id"]),
        role=str(row["role"]),  # type: ignore[arg-type]
        text=str(row["text"]),
        audio_url=audio_url,
        hiragana=(str(row["hiragana_text"]) if row.get("hiragana_text") else None),
        english=(str(row["english_text"]) if row.get("english_text") else None),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


def _lesson_info(next_lesson: NextLesson) -> LessonInfoOut:
    return LessonInfoOut(
        id=next_lesson.lesson_id,
        title_en=next_lesson.lesson_title_en,
        title_ja=next_lesson.lesson_title_ja,
        level=next_lesson.lesson_level,
        can_dos=list(next_lesson.lesson_can_dos),
        topic_title_en=next_lesson.topic_title_en,
        topic_title_ja=next_lesson.topic_title_ja,
    )


def _load_lesson_info(engine: Engine, lesson_id: int | None) -> LessonInfoOut | None:
    if lesson_id is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(lessons_table).where(lessons_table.c.id == lesson_id)
        ).mappings().one_or_none()
    if row is None:
        return None
    import json as _json

    try:
        can_dos = _json.loads(row["can_dos_json"] or "[]")
    except _json.JSONDecodeError:
        can_dos = []
    # We don't have the joined topic here; fetch it lazily below.
    from app.db import topics_table  # local import to keep top of file tidy

    with engine.connect() as conn:
        topic = conn.execute(
            select(topics_table).where(topics_table.c.id == row["topic_id"])
        ).mappings().one_or_none()

    return LessonInfoOut(
        id=int(row["id"]),
        title_en=str(row["title_en"]),
        title_ja=str(row["title_ja"]),
        level=str(row["level"]),
        can_dos=can_dos if isinstance(can_dos, list) else [],
        topic_title_en=str(topic["title_en"]) if topic else "",
        topic_title_ja=str(topic["title_ja"]) if topic else "",
    )


def _load_turns(engine: Engine, session_id: int) -> list[TurnOut]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(session_turns_table)
            .where(session_turns_table.c.session_id == session_id)
            .order_by(session_turns_table.c.id)
        ).mappings().all()
    return [_row_to_turn(r) for r in rows]


def _load_session(engine: Engine, session_id: int) -> Mapping[str, object] | None:
    with engine.connect() as conn:
        return conn.execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        ).mappings().one_or_none()


def _session_phrase_hints(
    engine: Engine, user: Mapping[str, object], session_row: Mapping[str, object]
) -> tuple[list[str], list[str]]:
    """Speech-adaptation hints for a voice turn.

    Returns (strong, vocab): the learner's name gets its own maximum-boost
    context so it wins ambiguous cases; the lesson vocabulary is a moderate
    boost.
    """
    plan_markdown: str | None = None
    plan_id = session_row.get("lesson_plan_id")
    if plan_id is not None:
        with engine.connect() as conn:
            row = conn.execute(
                select(lesson_plans_table.c.body_markdown).where(
                    lesson_plans_table.c.id == plan_id
                )
            ).one_or_none()
        if row is not None:
            plan_markdown = row[0]
    name = str(user.get("name_ja") or "").strip()
    strong = [name] if name else []
    vocab = extract_vocab_hints(plan_markdown)
    return strong, vocab


def _ensure_owned_active(
    engine: Engine, session_id: int, user_id: int
) -> Mapping[str, object]:
    row = _load_session(engine, session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if int(row["user_id"]) != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")
    if row["ended_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session has ended",
        )
    return row


def _build_messages_for_session(engine: Engine, session_id: int) -> list[Message]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(session_turns_table.c.role, session_turns_table.c.text)
            .where(session_turns_table.c.session_id == session_id)
            .order_by(session_turns_table.c.id)
        ).all()
    return [Message(role=r[0], content=r[1]) for r in rows if r[0] in ("user", "assistant")]


def _build_system_prompt(
    user: Mapping[str, object],
    session_row: Mapping[str, object],
    engine: Engine,
) -> str:
    lesson_info = _load_lesson_info(engine, session_row.get("lesson_id"))  # type: ignore[arg-type]
    plan_markdown: str | None = None
    plan_id = session_row.get("lesson_plan_id")
    if plan_id is not None:
        from app.db import lesson_plans_table

        with engine.connect() as conn:
            plan_row = conn.execute(
                select(lesson_plans_table.c.body_markdown).where(
                    lesson_plans_table.c.id == plan_id
                )
            ).one_or_none()
        if plan_row is not None:
            plan_markdown = str(plan_row[0])

    # Build a compact profile snapshot for the prompt.
    profile_snapshot = _build_profile_snapshot(engine, int(user["id"]))

    return build_tutor_system_prompt(
        user,
        lesson_title=lesson_info.title_en if lesson_info else None,
        lesson_can_dos=lesson_info.can_dos if lesson_info else None,
        lesson_plan_markdown=plan_markdown,
        mode=str(session_row.get("mode", "freeform")),
        profile_snapshot=profile_snapshot,
    )


def _build_profile_snapshot(engine: Engine, user_id: int) -> str | None:
    """Build a compact text block of the learner's profile for the prompt.

    Caps: top 30 vocab by recency, top 5 weakest grammar, top 5 recent
    mistakes, top 3 topic interests. Returns None if the profile is empty.
    """
    from app.db import (
        grammar_points_table,
        mistakes_table,
        topic_interests_table,
        vocab_items_table,
    )

    lines: list[str] = []
    with engine.connect() as conn:
        vocab_rows = conn.execute(
            select(vocab_items_table.c.jp, vocab_items_table.c.en, vocab_items_table.c.mastery)
            .where(vocab_items_table.c.user_id == user_id)
            .order_by(vocab_items_table.c.last_seen_at.desc())
            .limit(30)
        ).all()
        grammar_rows = conn.execute(
            select(grammar_points_table.c.code, grammar_points_table.c.mastery)
            .where(grammar_points_table.c.user_id == user_id)
            .order_by(grammar_points_table.c.mastery.asc())
            .limit(5)
        ).all()
        mistake_rows = conn.execute(
            select(mistakes_table.c.original, mistakes_table.c.corrected)
            .where(mistakes_table.c.user_id == user_id)
            .order_by(mistakes_table.c.id.desc())
            .limit(5)
        ).all()
        topic_rows = conn.execute(
            select(topic_interests_table.c.keyword)
            .where(topic_interests_table.c.user_id == user_id)
            .order_by(topic_interests_table.c.weight.desc())
            .limit(3)
        ).all()

    if vocab_rows:
        items = [f"{r[0]}({r[1]})" if r[1] else r[0] for r in vocab_rows]
        lines.append(f"Known vocab: {', '.join(items)}")
    if grammar_rows:
        items = [f"{r[0]}(mastery {r[1]})" for r in grammar_rows]
        lines.append(f"Weakest grammar: {', '.join(items)}")
    if mistake_rows:
        items = [f"{r[0]}→{r[1]}" for r in mistake_rows]
        lines.append(f"Recent mistakes: {'; '.join(items)}")
    if topic_rows:
        lines.append(f"Interests: {', '.join(r[0] for r in topic_rows)}")

    return "\n".join(lines) if lines else None


# Shown when the tutor returns nothing usable — a real sentence, never "…",
# so an empty API response can't leave the learner staring at a dead turn.
_FALLBACK_REPLY = "すみません、もう一度お願いします。"


def _tutor_reply(llm: object, history: list[Message], system: str) -> str:
    """Generate a tutor reply, robust to two real failure modes.

    1. Claude Sonnet 5+ rejects a request whose messages end with an assistant
       turn ("assistant message prefill" — a 400). Drop any trailing assistant
       turns so an anomalous/poisoned history can't hard-fail the turn.
    2. The API occasionally returns an empty message. Retry once, then fall
       back to a gentle prompt rather than saving an empty "…" turn that looks
       stuck (and would otherwise poison the conversation).
    """
    msgs = list(history)
    while msgs and msgs[-1].role == "assistant":
        msgs.pop()

    text = ""
    for _ in range(2):
        resp = llm.chat(msgs, system=system)  # type: ignore[attr-defined]
        text = (resp.text or "").strip()
        if text:
            return text
    logger.warning("Tutor returned an empty reply twice; using fallback")
    return _FALLBACK_REPLY


def _ensure_reading_aids(
    llm: object, user: Mapping[str, object], parsed: ParsedReply
) -> ParsedReply:
    """Guarantee the reading aids the learner asked for are present.

    The tutor is prompted to append [HIRAGANA]/[EN] lines inline, but in long
    conversations it sometimes drops them. When a requested aid is missing,
    backfill it with one focused, best-effort LLM call for the reply text —
    keeping the fast inline path when the tutor complied.
    """
    want_hira = bool(user.get("show_hiragana"))
    want_en = bool(user.get("show_english"))
    need_hira = want_hira and not parsed.hiragana
    need_en = want_en and not parsed.english
    if not parsed.text.strip() or not (need_hira or need_en):
        return parsed

    reqs: list[str] = []
    if need_hira:
        reqs.append(
            "[HIRAGANA] <the text rewritten with every kanji replaced by its "
            "hiragana reading; keep punctuation and existing kana as-is>"
        )
    if need_en:
        reqs.append("[EN] <a brief, natural English translation of the text>")
    aid_system = (
        "You add reading aids to a single Japanese message. Output ONLY the "
        "marker line(s) below, each on its own line — no original text, no "
        "commentary.\n" + "\n".join(reqs)
    )
    try:
        aid_resp = llm.chat(  # type: ignore[attr-defined]
            [Message(role="user", content=parsed.text)], system=aid_system
        )
    except Exception:
        logger.warning("Reading-aid backfill failed", exc_info=True)
        return parsed

    aid = parse_tutor_reply(aid_resp.text or "")
    return ParsedReply(
        text=parsed.text,
        hiragana=parsed.hiragana or (aid.hiragana if need_hira else None),
        english=parsed.english or (aid.english if need_en else None),
    )


def _append_turn(
    engine: Engine,
    session_id: int,
    *,
    role: str,
    text: str,
    audio_path: str | None = None,
    hiragana: str | None = None,
    english: str | None = None,
) -> Mapping[str, object]:
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        result = conn.execute(
            insert(session_turns_table).values(
                session_id=session_id,
                role=role,
                text=text,
                audio_path=audio_path,
                hiragana_text=hiragana,
                english_text=english,
                created_at=now,
            )
        )
        new_id = result.inserted_primary_key[0]
        row = conn.execute(
            select(session_turns_table).where(session_turns_table.c.id == new_id)
        ).mappings().one()
    return row


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/active", response_model=ActiveSessionOut)
def get_active(user: CurrentUser, engine: EngineDep) -> ActiveSessionOut:
    """Return the user's most recent unended session, plus the next queued lesson."""
    user_id = int(user["id"])
    with engine.connect() as conn:
        row = conn.execute(
            select(sessions_table)
            .where(sessions_table.c.user_id == user_id)
            .where(sessions_table.c.ended_at.is_(None))
            .order_by(sessions_table.c.id.desc())
            .limit(1)
        ).mappings().one_or_none()

    active: SessionDetailOut | None = None
    if row is not None:
        active = SessionDetailOut(
            session=_row_to_session(row),
            lesson=_load_lesson_info(engine, row.get("lesson_id")),  # type: ignore[arg-type]
            turns=_load_turns(engine, int(row["id"])),
        )

    next_lesson_pick = pick_next_lesson(engine, user_id)
    next_lesson = _lesson_info(next_lesson_pick) if next_lesson_pick else None

    return ActiveSessionOut(active=active, next_lesson=next_lesson)


@router.get("/next-lesson", response_model=LessonInfoOut | None)
def get_next_lesson(user: CurrentUser, engine: EngineDep) -> LessonInfoOut | None:
    pick = pick_next_lesson(engine, int(user["id"]))
    return _lesson_info(pick) if pick else None


@router.get("/lessons", response_model=list[LessonOptionOut])
def list_lesson_options(user: CurrentUser, engine: EngineDep) -> list[LessonOptionOut]:
    """All approved lessons the learner can pick, with their practice history.

    Ordered canonically (topic, then lesson). Practice stats count the
    learner's *finished* sessions on each lesson (ended_at set), matching the
    "completed" definition used by pick_next_lesson.
    """
    user_id = int(user["id"])

    # Per-user finished-session stats, keyed by lesson_id.
    with engine.connect() as conn:
        stat_rows = conn.execute(
            select(
                sessions_table.c.lesson_id,
                func.count().label("practiced_count"),
                func.max(sessions_table.c.ended_at).label("last_practiced_at"),
            )
            .where(sessions_table.c.user_id == user_id)
            .where(sessions_table.c.ended_at.is_not(None))
            .where(sessions_table.c.lesson_id.is_not(None))
            .group_by(sessions_table.c.lesson_id)
        ).mappings().all()
    stats = {r["lesson_id"]: r for r in stat_rows}

    with engine.connect() as conn:
        lesson_rows = conn.execute(
            select(
                lessons_table.c.id,
                lessons_table.c.title_en,
                lessons_table.c.title_ja,
                lessons_table.c.level,
                lessons_table.c.can_dos_json,
                topics_table.c.title_en.label("topic_title_en"),
                topics_table.c.title_ja.label("topic_title_ja"),
            )
            .select_from(
                lesson_plans_table.join(
                    lessons_table,
                    lessons_table.c.id == lesson_plans_table.c.lesson_id,
                ).join(
                    topics_table,
                    topics_table.c.id == lessons_table.c.topic_id,
                )
            )
            .where(lesson_plans_table.c.status == "approved")
            .order_by(
                topics_table.c.sort_order,
                lessons_table.c.sort_order,
                lessons_table.c.id,
            )
        ).mappings().all()

    options: list[LessonOptionOut] = []
    for row in lesson_rows:
        try:
            can_dos = json.loads(str(row["can_dos_json"] or "[]"))
            if not isinstance(can_dos, list):
                can_dos = []
        except json.JSONDecodeError:
            can_dos = []
        stat = stats.get(row["id"])
        options.append(
            LessonOptionOut(
                id=int(row["id"]),
                title_en=str(row["title_en"]),
                title_ja=str(row["title_ja"]),
                level=str(row["level"]),
                can_dos=can_dos,
                topic_title_en=str(row["topic_title_en"]),
                topic_title_ja=str(row["topic_title_ja"]),
                practiced_count=int(stat["practiced_count"]) if stat else 0,
                last_practiced_at=stat["last_practiced_at"] if stat else None,
            )
        )
    return options


@router.get("/lessons/{lesson_id}/study", response_model=LessonStudyOut)
def get_lesson_study(
    lesson_id: int, user: CurrentUser, engine: EngineDep
) -> LessonStudyOut:
    """The learner-facing study sections (Scenario / vocab / patterns) of an
    approved lesson, for display in the Practice screen."""
    with engine.connect() as conn:
        row = conn.execute(
            select(lesson_plans_table.c.body_markdown)
            .select_from(
                lesson_plans_table.join(
                    lessons_table,
                    lessons_table.c.id == lesson_plans_table.c.lesson_id,
                )
            )
            .where(lessons_table.c.id == lesson_id)
            .where(lesson_plans_table.c.status == "approved")
        ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No approved plan for this lesson",
        )
    return LessonStudyOut(
        lesson_id=lesson_id, study_markdown=extract_study_sections(row[0])
    )


@router.post("/start", response_model=SessionDetailOut, status_code=status.HTTP_201_CREATED)
def start_session(
    payload: StartSessionBody,
    user: CurrentUser,
    engine: EngineDep,
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
    speech: Annotated[SpeechProvider, Depends(get_speech_provider_dep)],
) -> SessionDetailOut:
    user_id = int(user["id"])

    # Pick the lesson.
    if payload.lesson_id is not None:
        chosen = get_lesson_for_session(engine, payload.lesson_id)
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lesson does not exist or has no approved plan.",
            )
    else:
        chosen = pick_next_lesson(engine, user_id)
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No approved lesson plans available. Ask the parent to approve one.",
            )

    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        result = conn.execute(
            insert(sessions_table).values(
                user_id=user_id,
                lesson_id=chosen.lesson_id,
                lesson_plan_id=chosen.plan_id,
                mode=payload.mode,
                tutor_voice=str(user["voice"]),
                llm_provider=str(user["llm_provider"]),
                speech_provider=str(user["speech_provider"]),
                started_at=now,
            )
        )
        session_id = int(result.inserted_primary_key[0])
        session_row = conn.execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        ).mappings().one()

    # Generate the opening greeting from the LLM.
    system_prompt = _build_system_prompt(user, session_row, engine)
    seed_message = Message(
        role="user",
        content=(
            "(The learner just sat down to start the lesson. "
            "Greet them warmly and invite them into the topic in 1-2 sentences.)"
        ),
    )
    try:
        opener = llm.chat([seed_message], system=system_prompt)  # type: ignore[attr-defined]
        opening_text = (opener.text or "").strip() or "こんにちは!"
    except Exception as exc:
        logger.warning("Opening greeting failed (%s); using fallback", exc)
        opening_text = f"こんにちは、{user['name']}さん!"

    parsed_opener = _ensure_reading_aids(llm, user, parse_tutor_reply(opening_text))
    opener_ja = parsed_opener.text or opening_text

    # Synthesize audio for the opening greeting.
    opener_audio_path: str | None = None
    try:
        voice_enum = TutorVoice.from_string(str(user["voice"]))
        synth = speech.synthesize(opener_ja, voice=voice_enum)
        settings = get_settings()
        audio_dir = settings.data_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        extension = "mp3" if synth.mime_type == "audio/mpeg" else "bin"
        filename = f"{uuid.uuid4().hex}.{extension}"
        (audio_dir / filename).write_bytes(synth.audio)
        opener_audio_path = filename
    except Exception as exc:
        logger.warning("Opening TTS failed (%s); session starts without audio", exc)

    _append_turn(
        engine,
        session_id,
        role="assistant",
        text=opener_ja,
        audio_path=opener_audio_path,
        hiragana=parsed_opener.hiragana,
        english=parsed_opener.english,
    )

    return SessionDetailOut(
        session=_row_to_session(session_row),
        lesson=_lesson_info(chosen),
        turns=_load_turns(engine, session_id),
    )


@router.post(
    "/start-from-image",
    response_model=SessionDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def start_session_from_image(
    user: CurrentUser,
    engine: EngineDep,
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
    speech: Annotated[SpeechProvider, Depends(get_speech_provider_dep)],
    image: Annotated[UploadFile, File()],
    mode: Annotated[SessionMode, Form()] = "freeform",
) -> SessionDetailOut:
    """Create an ad-hoc session seeded by an uploaded textbook image.

    The image is saved under {data_dir}/uploads/<user_id>/. The opening
    LLM call attaches the image and asks the tutor to identify the topic
    and propose a short practice. Subsequent turns use the regular session
    flow (no need to re-attach the image; the conversation carries forward).
    """
    user_id = int(user["id"])
    image_bytes = image.file.read()
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image is empty",
        )
    if len(image_bytes) > 10 * 1024 * 1024:  # 10 MB safety cap
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="image must be 10 MB or smaller",
        )
    mime = detect_image_mime(image_bytes)
    if mime is None:
        # The bytes don't match any image format we recognise. This commonly
        # happens when a video (e.g., a .mp4 from a phone camera) is uploaded
        # by mistake, or when a HEIC variant we don't support yet is sent.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "That doesn't look like a supported image. Please upload a JPEG, "
                "PNG, WebP, GIF, or HEIC photo of the textbook page. Videos and "
                "live photos aren't supported — open the file and re-export it "
                "as a still image."
            ),
        )
    relative_path = save_upload(user_id, image_bytes, mime)

    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        result = conn.execute(
            insert(sessions_table).values(
                user_id=user_id,
                lesson_id=None,
                lesson_plan_id=None,
                mode=mode,
                tutor_voice=str(user["voice"]),
                llm_provider=str(user["llm_provider"]),
                speech_provider=str(user["speech_provider"]),
                started_at=now,
                seed_image_path=relative_path,
            )
        )
        session_id = int(result.inserted_primary_key[0])
        session_row = conn.execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        ).mappings().one()

    # Special seed prompt: ask the tutor to look at the image and propose a
    # short practice based on what's there.
    system_prompt = build_tutor_system_prompt(
        user,
        lesson_title=None,
        lesson_can_dos=None,
        lesson_plan_markdown=None,
        mode=mode,
    )
    seed_message = Message(
        role="user",
        content=(
            "(The learner just uploaded this image. It is almost certainly a "
            "Japanese textbook page or worksheet. Examine it closely and "
            "identify: (a) the lesson topic, and (b) at least two specific "
            "Japanese words, phrases, or example sentences that are "
            "actually visible on the page. Then, in Japanese at the "
            "learner's level, write a short opener that does ALL of the "
            "following:\n"
            "  1. Greet them warmly and introduce yourself.\n"
            "  2. Name the topic of the page.\n"
            "  3. Quote two specific words, phrases, or example sentences "
            "you can see on the page (use the exact Japanese from the "
            "image, in 「」 quotes).\n"
            "  4. Propose a short practice activity that USES those exact "
            "words/phrases.\n"
            "Keep it natural — 3 to 5 short sentences total. Then wait for "
            "their reply. If you genuinely cannot read any Japanese on the "
            "image, say so plainly in Japanese rather than guessing.)"
        ),
    )
    try:
        opener = llm.chat(  # type: ignore[attr-defined]
            [seed_message], system=system_prompt, images=[image_bytes]
        )
    except Exception as exc:
        # Roll back the half-created session so the learner can retry with a
        # different image without ending up in a half-good state.
        logger.exception("Image-seeded opening LLM call failed")
        with engine.begin() as conn:
            conn.execute(sessions_table.delete().where(sessions_table.c.id == session_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"The tutor couldn't process this image ({exc}). "
                "Please try a clearer photo or a different format."
            ),
        ) from exc

    opening_text = (opener.text or "").strip()
    if not opening_text:
        # The LLM returned nothing useful. Don't pretend it saw the image.
        with engine.begin() as conn:
            conn.execute(sessions_table.delete().where(sessions_table.c.id == session_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The tutor didn't return a response for this image. "
                "Please try a clearer photo of the page."
            ),
        )

    parsed_opener = _ensure_reading_aids(llm, user, parse_tutor_reply(opening_text))
    opener_ja = parsed_opener.text or opening_text

    # Synthesize audio for the opening greeting.
    opener_audio_path: str | None = None
    try:
        voice_enum = TutorVoice.from_string(str(user["voice"]))
        synth = speech.synthesize(opener_ja, voice=voice_enum)
        settings = get_settings()
        audio_dir = settings.data_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        extension = "mp3" if synth.mime_type == "audio/mpeg" else "bin"
        filename = f"{uuid.uuid4().hex}.{extension}"
        (audio_dir / filename).write_bytes(synth.audio)
        opener_audio_path = filename
    except Exception as exc:
        logger.warning("Image-seeded opening TTS failed (%s)", exc)

    _append_turn(
        engine,
        session_id,
        role="assistant",
        text=opener_ja,
        audio_path=opener_audio_path,
        hiragana=parsed_opener.hiragana,
        english=parsed_opener.english,
    )

    return SessionDetailOut(
        session=_row_to_session(session_row),
        lesson=None,
        turns=_load_turns(engine, session_id),
    )


@router.get("", response_model=list[SessionOut])
def list_sessions(user: CurrentUser, engine: EngineDep) -> list[SessionOut]:
    """Recent sessions (any state), most recent first."""
    user_id = int(user["id"])
    with engine.connect() as conn:
        rows = conn.execute(
            select(sessions_table)
            .where(sessions_table.c.user_id == user_id)
            .order_by(sessions_table.c.id.desc())
            .limit(50)
        ).mappings().all()
    return [_row_to_session(r) for r in rows]


@router.get("/{session_id}", response_model=SessionDetailOut)
def get_session(session_id: int, user: CurrentUser, engine: EngineDep) -> SessionDetailOut:
    row = _load_session(engine, session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if int(row["user_id"]) != int(user["id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")
    return SessionDetailOut(
        session=_row_to_session(row),
        lesson=_load_lesson_info(engine, row.get("lesson_id")),  # type: ignore[arg-type]
        turns=_load_turns(engine, session_id),
    )


@router.post("/{session_id}/turn", response_model=SessionDetailOut)
def text_turn(
    session_id: int,
    payload: TextTurnBody,
    user: CurrentUser,
    engine: EngineDep,
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
) -> SessionDetailOut:
    session_row = _ensure_owned_active(engine, session_id, int(user["id"]))

    _append_turn(engine, session_id, role="user", text=payload.content.strip())

    history = _build_messages_for_session(engine, session_id)
    system_prompt = _build_system_prompt(user, session_row, engine)
    try:
        reply_text = _tutor_reply(llm, history, system_prompt)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {exc}",
        ) from exc

    parsed_reply = _ensure_reading_aids(llm, user, parse_tutor_reply(reply_text))
    _append_turn(
        engine,
        session_id,
        role="assistant",
        text=parsed_reply.text or reply_text,
        hiragana=parsed_reply.hiragana,
        english=parsed_reply.english,
    )

    return SessionDetailOut(
        session=_row_to_session(session_row),
        lesson=_load_lesson_info(engine, session_row.get("lesson_id")),  # type: ignore[arg-type]
        turns=_load_turns(engine, session_id),
    )


@router.post("/{session_id}/turn-audio", response_model=SessionDetailOut)
def voice_turn(
    session_id: int,
    user: CurrentUser,
    engine: EngineDep,
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
    speech: Annotated[SpeechProvider, Depends(get_speech_provider_dep)],
    audio: Annotated[UploadFile, File()],
) -> SessionDetailOut:
    session_row = _ensure_owned_active(engine, session_id, int(user["id"]))

    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audio file is empty")

    strong_hints, vocab_hints = _session_phrase_hints(engine, user, session_row)
    try:
        transcript = speech.transcribe(
            audio_bytes,
            phrase_hints=vocab_hints,
            strong_hints=strong_hints,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Speech-to-text error: {exc}",
        ) from exc
    if not transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not detect any speech in the recording. Try again.",
        )

    _append_turn(engine, session_id, role="user", text=transcript.strip())

    history = _build_messages_for_session(engine, session_id)
    system_prompt = _build_system_prompt(user, session_row, engine)
    try:
        reply_text = _tutor_reply(llm, history, system_prompt)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {exc}",
        ) from exc

    parsed_reply = _ensure_reading_aids(llm, user, parse_tutor_reply(reply_text))
    ja_for_tts = parsed_reply.text or reply_text

    voice_enum = TutorVoice.from_string(str(user["voice"]))
    try:
        synth = speech.synthesize(ja_for_tts, voice=voice_enum)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Text-to-speech error: {exc}",
        ) from exc

    settings = get_settings()
    audio_dir = settings.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    extension = "mp3" if synth.mime_type == "audio/mpeg" else "bin"
    filename = f"{uuid.uuid4().hex}.{extension}"
    (audio_dir / filename).write_bytes(synth.audio)

    _append_turn(
        engine,
        session_id,
        role="assistant",
        text=ja_for_tts,
        audio_path=filename,
        hiragana=parsed_reply.hiragana,
        english=parsed_reply.english,
    )

    return SessionDetailOut(
        session=_row_to_session(session_row),
        lesson=_load_lesson_info(engine, session_row.get("lesson_id")),  # type: ignore[arg-type]
        turns=_load_turns(engine, session_id),
    )


def _save_audio_file(audio_bytes: bytes, mime_type: str) -> str:
    settings = get_settings()
    audio_dir = settings.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    extension = "mp3" if mime_type == "audio/mpeg" else "bin"
    filename = f"{uuid.uuid4().hex}.{extension}"
    (audio_dir / filename).write_bytes(audio_bytes)
    return filename


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_voice_reply(
    engine: Engine,
    session_row: Mapping[str, object],
    user: Mapping[str, object],
    llm: object,
    speech: SpeechProvider,
    session_id: int,
    transcript: str,
) -> Iterator[str]:
    """SSE generator for a voice turn: stream the tutor's text sentence by
    sentence, synthesizing and emitting audio for each sentence as soon as it
    completes, so playback starts well before the full reply exists.

    Runs as a sync generator — Starlette iterates it in a threadpool, so the
    blocking LLM/TTS SDK calls never block the event loop.

    Event order: transcript → (text, audio)* → aids → done. The reading-aid
    backfill (when the tutor drops [HIRAGANA]/[EN]) happens AFTER all audio
    has been emitted, so it is no longer on the listening-latency path.
    """
    yield _sse("transcript", {"text": transcript})

    history = _build_messages_for_session(engine, session_id)
    system_prompt = _build_system_prompt(user, session_row, engine)

    # Prefill guard: claude-sonnet-5 rejects histories ending in an
    # assistant turn (same guard as _tutor_reply).
    msgs = list(history)
    while msgs and msgs[-1].role == "assistant":
        msgs.pop()

    voice_enum = TutorVoice.from_string(str(user["voice"]))
    streamer = SentenceStreamer()
    audio_parts: list[bytes] = []
    audio_mime = "audio/mpeg"

    def sentence_events(sentence: str) -> Iterator[str]:
        nonlocal audio_mime
        yield _sse("text", {"delta": sentence})
        try:
            synth = speech.synthesize(sentence, voice=voice_enum)
        except Exception:
            logger.warning("Chunked TTS failed for a sentence", exc_info=True)
            return
        if synth.audio:
            audio_parts.append(synth.audio)
            audio_mime = synth.mime_type
            yield _sse(
                "audio",
                {
                    "b64": base64.b64encode(synth.audio).decode("ascii"),
                    "mime": synth.mime_type,
                },
            )

    stream_fn = getattr(llm, "stream_chat", None)
    try:
        if stream_fn is not None:
            deltas: Iterator[str] = stream_fn(msgs, system=system_prompt)
        else:
            # Provider without streaming: one blocking LLM call (with the
            # usual retry/fallback), then still pipeline the TTS chunks.
            deltas = iter([_tutor_reply(llm, msgs, system_prompt)])

        for delta in deltas:
            for sentence in streamer.feed(delta):
                yield from sentence_events(sentence)
        rest = streamer.flush()
        if rest:
            yield from sentence_events(rest)
    except Exception as exc:
        if not streamer.full_text.strip():
            logger.exception("Streaming LLM call failed before any text")
            yield _sse("error", {"detail": f"LLM provider error: {exc}"})
            return
        # Partial reply: keep what we have rather than dropping the turn.
        logger.warning("LLM stream failed mid-reply; keeping partial text", exc_info=True)

    full_text = streamer.full_text.strip()
    if not full_text:
        # Empty stream — retry via the non-streaming path (which itself
        # retries once and then falls back to a gentle prompt).
        try:
            full_text = _tutor_reply(llm, msgs, system_prompt).strip()
        except Exception as exc:
            yield _sse("error", {"detail": f"LLM provider error: {exc}"})
            return
        retry_streamer = SentenceStreamer()
        for sentence in retry_streamer.feed(full_text):
            yield from sentence_events(sentence)
        rest = retry_streamer.flush()
        if rest:
            yield from sentence_events(rest)

    parsed_reply = parse_tutor_reply(full_text)
    ja_for_tts = parsed_reply.text or full_text

    # If chunked synthesis produced nothing (e.g. every sentence call
    # failed), try once for the whole reply so replay still works.
    if not audio_parts and ja_for_tts.strip():
        try:
            synth = speech.synthesize(ja_for_tts, voice=voice_enum)
            if synth.audio:
                audio_parts.append(synth.audio)
                audio_mime = synth.mime_type
                yield _sse(
                    "audio",
                    {
                        "b64": base64.b64encode(synth.audio).decode("ascii"),
                        "mime": synth.mime_type,
                    },
                )
        except Exception:
            logger.warning("Whole-reply TTS fallback failed", exc_info=True)

    # Reading-aid backfill — off the audio path by design (audio already sent).
    parsed_reply = _ensure_reading_aids(llm, user, parsed_reply)
    yield _sse(
        "aids",
        {"hiragana": parsed_reply.hiragana, "english": parsed_reply.english},
    )

    audio_path: str | None = None
    if audio_parts:
        audio_path = _save_audio_file(b"".join(audio_parts), audio_mime)

    _append_turn(
        engine,
        session_id,
        role="assistant",
        text=ja_for_tts,
        audio_path=audio_path,
        hiragana=parsed_reply.hiragana,
        english=parsed_reply.english,
    )

    detail = SessionDetailOut(
        session=_row_to_session(session_row),
        lesson=_load_lesson_info(engine, session_row.get("lesson_id")),  # type: ignore[arg-type]
        turns=_load_turns(engine, session_id),
    )
    yield _sse("done", detail.model_dump(mode="json"))


@router.post("/{session_id}/turn-audio/stream")
def voice_turn_stream(
    session_id: int,
    user: CurrentUser,
    engine: EngineDep,
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
    speech: Annotated[SpeechProvider, Depends(get_speech_provider_dep)],
    audio: Annotated[UploadFile, File()],
) -> StreamingResponse:
    """Streaming variant of voice_turn (SSE).

    STT still runs before the response starts (so STT errors surface as
    normal HTTP errors and the client can fall back), then the LLM reply is
    streamed sentence-by-sentence with per-sentence TTS. The persisted turn
    (full text + one combined audio file + reading aids) is identical to the
    non-streaming endpoint, so history and replay are unaffected.
    """
    session_row = _ensure_owned_active(engine, session_id, int(user["id"]))

    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audio file is empty")

    strong_hints, vocab_hints = _session_phrase_hints(engine, user, session_row)
    try:
        transcript = speech.transcribe(
            audio_bytes,
            phrase_hints=vocab_hints,
            strong_hints=strong_hints,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Speech-to-text error: {exc}",
        ) from exc
    if not transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not detect any speech in the recording. Try again.",
        )

    _append_turn(engine, session_id, role="user", text=transcript.strip())

    return StreamingResponse(
        _stream_voice_reply(
            engine, session_row, user, llm, speech, session_id, transcript.strip()
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering for SSE
        },
    )


@router.post("/{session_id}/end", response_model=SessionOut)
def end_session(
    session_id: int,
    user: CurrentUser,
    engine: EngineDep,
    llm: Annotated[object, Depends(get_provider_for_user_dep)],
) -> SessionOut:
    session_row = _load_session(engine, session_id)
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if int(session_row["user_id"]) != int(user["id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")
    if session_row["ended_at"] is not None:
        return _row_to_session(session_row)

    # If the learner uses end-of-session corrections, generate a brief summary
    # of mistakes / suggestions from the transcript before closing the session.
    summary: str | None = None
    if str(user.get("correction_style", "end_of_turn")) == "end_of_session":
        history = _build_messages_for_session(engine, session_id)
        if history:
            summary_request = (
                "(System: the learner has just ended this practice session. "
                "Write a short wrap-up for them in their preferred language. "
                "List 2-4 specific corrections or suggestions you'd offer based "
                "on the conversation above. Keep it kind and concrete. "
                "Don't repeat the conversation; focus on actionable feedback.)"
            )
            history_for_summary = [*history, Message(role="user", content=summary_request)]
            try:
                response = llm.chat(  # type: ignore[attr-defined]
                    history_for_summary,
                    system=_build_system_prompt(user, session_row, engine),
                )
                summary = (response.text or "").strip() or None
            except Exception as exc:
                logger.warning("End-of-session summary generation failed: %s", exc)

    now = datetime.now(UTC).replace(tzinfo=None)
    update_values: dict[str, object] = {"ended_at": now}
    if summary is not None:
        update_values["summary"] = summary

    with engine.begin() as conn:
        conn.execute(
            update(sessions_table)
            .where(sessions_table.c.id == session_id)
            .values(**update_values)
        )
        new_row = conn.execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        ).mappings().one()

    # Run learning-profile extraction on the transcript. This is best-effort:
    # any failure is logged and the session still ends cleanly.
    try:
        from app.profile.extract import extract_and_persist

        extract_and_persist(engine, llm, user, session_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Profile extraction failed for session %s: %s", session_id, exc)

    return _row_to_session(new_row)
