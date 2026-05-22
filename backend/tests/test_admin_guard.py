"""Tests for the current_user dependency and admin guard."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_admin_whoami_requires_x_user_id(client: TestClient) -> None:
    response = client.get("/api/admin/whoami")
    assert response.status_code == 401


def test_admin_whoami_rejects_unknown_user(client: TestClient) -> None:
    response = client.get("/api/admin/whoami", headers={"X-User-Id": "999"})
    assert response.status_code == 401


def test_admin_whoami_rejects_non_admin(client: TestClient) -> None:
    user = client.post("/api/users", json={"name": "Kid"}).json()
    response = client.get("/api/admin/whoami", headers={"X-User-Id": str(user["id"])})
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin only"


def test_admin_whoami_allows_admin(client: TestClient) -> None:
    user = client.post("/api/users", json={"name": "Mom"}).json()
    client.patch(f"/api/users/{user['id']}", json={"is_admin": True})
    response = client.get("/api/admin/whoami", headers={"X-User-Id": str(user["id"])})
    assert response.status_code == 200
    body = response.json()
    assert body == {"id": user["id"], "name": "Mom", "is_admin": True}


def test_x_user_id_must_be_integer(client: TestClient) -> None:
    response = client.get("/api/admin/whoami", headers={"X-User-Id": "not-a-number"})
    assert response.status_code == 400
