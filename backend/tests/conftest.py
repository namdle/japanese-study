"""Shared test fixtures.

Each test gets its own temp data directory so the SQLite file is isolated
and the WAL pragma is exercised on a fresh connection.

The Settings model reads APP_DATA_DIR from the environment, so we override
it via monkeypatch and clear the lru_cache on get_settings.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import reset_engine_for_tests
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Override settings to use a temp data directory for this test."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    reset_engine_for_tests()
    try:
        yield Settings()
    finally:
        get_settings.cache_clear()
        reset_engine_for_tests()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:  # noqa: ARG001 - fixture activates env override
    app = create_app()
    with TestClient(app) as c:
        yield c
