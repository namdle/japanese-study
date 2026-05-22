"""End-to-end tests for /api/users CRUD."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_users_list_starts_empty(client: TestClient) -> None:
    response = client.get("/api/users")
    assert response.status_code == 200
    assert response.json() == []


def test_create_user_returns_full_profile(client: TestClient) -> None:
    response = client.post("/api/users", json={"name": "Mom"})
    assert response.status_code == 201
    body = response.json()

    # Sensible defaults are applied server-side.
    assert body["id"] >= 1
    assert body["name"] == "Mom"
    assert body["is_admin"] is False
    assert body["level"] == "A1"
    assert body["voice"] == "Misa"
    assert body["llm_provider"] == "claude"
    assert body["speech_provider"] == "gcloud"
    assert body["correction_style"] == "end_of_turn"
    assert body["explanation_language"] == "en"
    assert body["show_hiragana"] is False
    assert body["show_english"] is False
    assert "created_at" in body


def test_create_user_trims_and_rejects_blank(client: TestClient) -> None:
    assert client.post("/api/users", json={"name": "  "}).status_code == 422
    assert client.post("/api/users", json={"name": "  Kid1  "}).json()["name"] == "Kid1"


def test_create_user_conflict_on_duplicate_name(client: TestClient) -> None:
    client.post("/api/users", json={"name": "Kid"})
    response = client.post("/api/users", json={"name": "Kid"})
    assert response.status_code == 409
    assert "already taken" in response.json()["detail"].lower()


def test_get_user_by_id(client: TestClient) -> None:
    created = client.post("/api/users", json={"name": "Kid"}).json()
    response = client.get(f"/api/users/{created['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "Kid"


def test_get_user_404_for_unknown(client: TestClient) -> None:
    assert client.get("/api/users/999").status_code == 404


def test_patch_user_updates_fields(client: TestClient) -> None:
    created = client.post("/api/users", json={"name": "Kid"}).json()
    response = client.patch(
        f"/api/users/{created['id']}",
        json={"name": "Sora", "voice": "Hiro", "level": "A2", "is_admin": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Sora"
    assert body["voice"] == "Hiro"
    assert body["level"] == "A2"
    assert body["is_admin"] is True


def test_patch_user_400_when_no_fields(client: TestClient) -> None:
    created = client.post("/api/users", json={"name": "Kid"}).json()
    response = client.patch(f"/api/users/{created['id']}", json={})
    assert response.status_code == 400


def test_patch_user_404_for_unknown(client: TestClient) -> None:
    assert client.patch("/api/users/999", json={"name": "Foo"}).status_code == 404


def test_patch_user_409_on_name_conflict(client: TestClient) -> None:
    a = client.post("/api/users", json={"name": "A"}).json()
    client.post("/api/users", json={"name": "B"}).json()
    response = client.patch(f"/api/users/{a['id']}", json={"name": "B"})
    assert response.status_code == 409


def test_delete_user(client: TestClient) -> None:
    created = client.post("/api/users", json={"name": "Tmp"}).json()
    response = client.delete(f"/api/users/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get(f"/api/users/{created['id']}").status_code == 404


def test_delete_user_404_for_unknown(client: TestClient) -> None:
    assert client.delete("/api/users/999").status_code == 404


def test_list_users_sorted_by_name(client: TestClient) -> None:
    for n in ["Charlie", "Alice", "Bob"]:
        client.post("/api/users", json={"name": n})
    names = [u["name"] for u in client.get("/api/users").json()]
    assert names == ["Alice", "Bob", "Charlie"]
