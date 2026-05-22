"""Test the /api/healthz endpoint and basic startup."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/api/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "japanese-study-backend"}


def test_healthz_is_under_api_prefix(client: TestClient) -> None:
    """Sanity check: the unprefixed /healthz should not exist."""
    response = client.get("/healthz")
    assert response.status_code == 404
