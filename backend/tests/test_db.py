"""Verify SQLite is initialized with WAL and foreign keys, and init_db is idempotent."""

from __future__ import annotations

from sqlalchemy import text

from app.config import Settings
from app.db import create_app_engine, init_db


def test_init_db_creates_data_dir_and_db_file(settings: Settings) -> None:
    assert not settings.data_dir.exists() or not settings.database_path.exists()
    init_db(create_app_engine(settings))
    assert settings.data_dir.exists()
    assert settings.database_path.exists()


def test_sqlite_pragmas_are_set(settings: Settings) -> None:
    engine = create_app_engine(settings)
    init_db(engine)
    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert str(journal_mode).lower() == "wal"
    assert int(foreign_keys) == 1


def test_init_db_is_idempotent(settings: Settings) -> None:
    engine = create_app_engine(settings)
    init_db(engine)
    # Second call must not raise
    init_db(engine)
