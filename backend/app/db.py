"""SQLite database setup with SQLAlchemy Core.

- Single MetaData object with table definitions for the whole app.
- WAL journal mode and foreign_keys ON, applied via event.listen on connect.
- init_db() creates the data directory and all tables (idempotent).

Tables in this module are introduced incrementally per task; later tasks add
Table() definitions to the same MetaData.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

metadata = MetaData()


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

def _utcnow() -> datetime:
    """Naive-but-UTC timestamp default. SQLite stores TEXT or numeric."""
    return datetime.now(UTC).replace(tzinfo=None)


users_table = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False, unique=True),
    # Optional Japanese rendering of the name (katakana/hiragana). Used to bias
    # speech-to-text so a name like "Nam" (ナム) isn't heard as 眠い.
    Column("name_ja", String, nullable=False, default=""),
    Column("is_admin", Integer, nullable=False, default=0),
    Column("level", String, nullable=False, default="A1"),
    Column("voice", String, nullable=False, default="Misa"),
    Column("llm_provider", String, nullable=False, default="claude"),
    Column("speech_provider", String, nullable=False, default="gcloud"),
    # correction_style: when corrections are surfaced. 'end_of_turn' | 'end_of_session'.
    Column("correction_style", String, nullable=False, default="end_of_turn"),
    # explanation_language: 'en' (English explanations) | 'ja' (immersion).
    Column("explanation_language", String, nullable=False, default="en"),
    # Optional reading aids displayed under each tutor turn.
    Column("show_hiragana", Integer, nullable=False, default=0),
    Column("show_english", Integer, nullable=False, default=0),
    # Seconds of silence (after the learner has spoken) before "Auto-stop" mode
    # ends a recording on its own; the mic can always be stopped manually. Only
    # used when the learner enables the Auto-stop checkbox in the Practice window.
    Column("auto_stop_seconds", Integer, nullable=False, default=2),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
)


# Curriculum
# A topic groups related conversation themes (e.g., "Family & Home").
topics_table = Table(
    "topics",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String, nullable=False, unique=True),
    Column("title_en", String, nullable=False),
    Column("title_ja", String, nullable=False),
    Column("kid_friendly", Integer, nullable=False, default=1),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
)

# A lesson is a specific can-do oriented practice within a topic.
lessons_table = Table(
    "lessons",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "topic_id",
        Integer,
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("code", String, nullable=False, unique=True),
    Column("title_en", String, nullable=False),
    Column("title_ja", String, nullable=False),
    # CEFR level: A1 / A2 / B1 / B2 / C1
    Column("level", String, nullable=False, default="A1"),
    # JSON-encoded list of can-do statements (kept as TEXT for SQLite).
    Column("can_dos_json", Text, nullable=False, default="[]"),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
)

# A lesson plan is admin-authored markdown that becomes the LLM input.
# Status is 'draft' or 'approved'; only 'approved' plans drive sessions.
lesson_plans_table = Table(
    "lesson_plans",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "lesson_id",
        Integer,
        ForeignKey("lessons.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("body_markdown", Text, nullable=False, default=""),
    Column("status", String, nullable=False, default="draft"),
    Column("version", Integer, nullable=False, default=1),
    Column("updated_at", DateTime, nullable=False, default=_utcnow),
    Column(
        "updated_by",
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    # One plan per lesson (we update in place rather than versioning rows).
    UniqueConstraint("lesson_id", name="uq_lesson_plans_lesson_id"),
)


# A session captures a single practice run: persona, lesson, mode, and the
# turn-by-turn transcript.
sessions_table = Table(
    "sessions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "lesson_id",
        Integer,
        ForeignKey("lessons.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column(
        "lesson_plan_id",
        Integer,
        ForeignKey("lesson_plans.id", ondelete="SET NULL"),
        nullable=True,
    ),
    # 'freeform' | 'three_phase' (Task 9 wires up the latter)
    Column("mode", String, nullable=False, default="freeform"),
    Column("tutor_voice", String, nullable=False, default="Misa"),
    Column("llm_provider", String, nullable=False, default="claude"),
    Column("speech_provider", String, nullable=False, default="gcloud"),
    Column("started_at", DateTime, nullable=False, default=_utcnow),
    Column("ended_at", DateTime, nullable=True),
    Column("summary", Text, nullable=True),
    # JSON snapshot of the learner profile at session start (Task 12).
    Column("profile_snapshot_json", Text, nullable=True),
    # Path (relative to data_dir) of the image used to seed this session, if any.
    Column("seed_image_path", String, nullable=True),
)


# A turn is one entry in the conversation log. role is 'user' | 'assistant'
# (system messages live in the prompt, not the turn log).
session_turns_table = Table(
    "session_turns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "session_id",
        Integer,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", String, nullable=False),
    Column("text", Text, nullable=False),
    Column("audio_path", String, nullable=True),
    Column("corrections_json", Text, nullable=True),
    # Optional reading aids generated alongside the Japanese reply.
    Column("hiragana_text", Text, nullable=True),
    Column("english_text", Text, nullable=True),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
)


# ---------------------------------------------------------------------------
# Learning profile (Task 11+): captured after each session.
# ---------------------------------------------------------------------------

# Vocabulary the learner has encountered, with simple mastery tracking.
vocab_items_table = Table(
    "vocab_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("jp", String, nullable=False),
    Column("reading", String, nullable=True),
    Column("en", String, nullable=True),
    # Mastery: 0 (struggling) .. 5 (solid). +1 per correct use, -1 per mistake.
    Column("mastery", Integer, nullable=False, default=1),
    Column(
        "first_session_id",
        Integer,
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("last_seen_at", DateTime, nullable=False, default=_utcnow),
    UniqueConstraint("user_id", "jp", name="uq_vocab_items_user_jp"),
)

# Grammar points (e.g., "te-form-request") encountered/used.
grammar_points_table = Table(
    "grammar_points",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("code", String, nullable=False),
    Column("example_jp", String, nullable=True),
    Column("notes", Text, nullable=True),
    Column("mastery", Integer, nullable=False, default=1),
    Column("last_seen_at", DateTime, nullable=False, default=_utcnow),
    UniqueConstraint("user_id", "code", name="uq_grammar_points_user_code"),
)

# Mistake log: every recorded slip, append-only.
mistakes_table = Table(
    "mistakes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "session_id",
        Integer,
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("mistake_type", String, nullable=True),
    Column("original", String, nullable=False),
    Column("corrected", String, nullable=False),
    Column("note", Text, nullable=True),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
)

# Recurring topic interests, weighted by exposure.
topic_interests_table = Table(
    "topic_interests",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("keyword", String, nullable=False),
    Column("weight", Integer, nullable=False, default=1),
    Column("last_seen_at", DateTime, nullable=False, default=_utcnow),
    UniqueConstraint("user_id", "keyword", name="uq_topic_interests_user_keyword"),
)


def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Enable WAL journal mode and foreign keys for every new SQLite connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        # synchronous=NORMAL is the typical pairing with WAL: durable enough
        # for our use case, faster than FULL.
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_app_engine(settings: Settings | None = None) -> Engine:
    """Create the SQLAlchemy engine for the app's SQLite database.

    Ensures the data directory exists and registers the SQLite pragma listener.
    """
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        settings.database_url,
        future=True,
        # SQLite + FastAPI threadpool: allow connections to be used across
        # threads. SQLAlchemy still serializes access at the connection level.
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


# Module-level engine, created lazily so tests can override settings first.
_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_app_engine()
    return _engine


def reset_engine_for_tests() -> None:
    """Dispose any cached engine so tests can start fresh with new settings."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_db(engine: Engine | None = None) -> None:
    """Bootstrap the schema on app startup.

    Idempotent: SQLAlchemy emits CREATE TABLE IF NOT EXISTS for every table
    in metadata. Then we seed the curriculum taxonomy (topics + lessons).
    """
    engine = engine or get_engine()
    metadata.create_all(engine)
    _apply_additive_migrations(engine)
    db_path: Path = engine.url.database  # type: ignore[assignment]
    logger.info("Database ready at %s (WAL=on, foreign_keys=on)", db_path)

    # Local import to avoid a circular dep (curriculum imports tables from db).
    from app.curriculum.seed import seed_curriculum

    inserted = seed_curriculum(engine)
    if inserted["topics"] or inserted["lessons"]:
        logger.info(
            "Curriculum seeded: +%d topics, +%d lessons",
            inserted["topics"],
            inserted["lessons"],
        )


def _apply_additive_migrations(engine: Engine) -> None:
    """Add columns that were introduced after a database may have been created.

    SQLAlchemy's create_all only creates tables; it doesn't add columns to
    existing tables. We use SQLite's PRAGMA table_info to detect missing
    columns and ALTER TABLE ADD COLUMN to add them. This keeps migrations
    simple without bringing in alembic — fine for our family-scale app.
    """
    additions: list[tuple[str, str, str]] = [
        # (table, column, sql definition fragment)
        ("users", "show_hiragana", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "show_english", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "auto_stop_seconds", "INTEGER NOT NULL DEFAULT 2"),
        ("users", "name_ja", "TEXT NOT NULL DEFAULT ''"),
        ("session_turns", "hiragana_text", "TEXT"),
        ("session_turns", "english_text", "TEXT"),
        ("sessions", "seed_image_path", "TEXT"),
    ]
    with engine.begin() as conn:
        for table, column, definition in additions:
            existing = {
                row[1]
                for row in conn.execute(sql_text(f"PRAGMA table_info({table})"))
            }
            if column not in existing:
                conn.execute(
                    sql_text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                )
                logger.info("Added column %s.%s", table, column)
