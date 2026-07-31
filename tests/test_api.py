from tests.conftest import DEV_ACTOR_ID


def actor_headers() -> dict[str, str]:
    return {"X-Development-Actor": str(DEV_ACTOR_ID)}


def create_content(client):  # type: ignore[no-untyped-def]
    response = client.post(
        "/api/v1/content",
        headers=actor_headers(),
        json={"title": "Incident footage", "source_url": "https://approved.example/item/1"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_and_ready(client):  # type: ignore[no-untyped-def]
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_content_create_retrieve_and_audit(client):  # type: ignore[no-untyped-def]
    item = create_content(client)
    assert client.get(f"/api/v1/content/{item['id']}").json()["title"] == "Incident footage"
    assert client.get("/api/v1/content").json()["total"] == 1
    assert (
        client.get(f"/api/v1/content/{item['id']}/audit").json()[0]["event_name"]
        == "content.created"
    )


def test_duplicate_source_is_rejected(client):  # type: ignore[no-untyped-def]
    create_content(client)
    duplicate = client.post(
        "/api/v1/content",
        headers=actor_headers(),
        json={"title": "Duplicate", "source_url": "https://approved.example/item/1"},
    )
    assert duplicate.status_code == 409


def test_forbidden_transition_and_required_actor(client):  # type: ignore[no-untyped-def]
    item = create_content(client)
    denied = client.post(
        f"/api/v1/content/{item['id']}/transition",
        json={"target_status": "IMPORTED", "reason": "test"},
    )
    assert denied.status_code == 401
    invalid = client.post(
        f"/api/v1/content/{item['id']}/transition",
        headers=actor_headers(),
        json={"target_status": "PUBLISHED", "reason": "skip"},
    )
    assert invalid.status_code == 409


def test_transition_creates_audit_event(client):  # type: ignore[no-untyped-def]
    item = create_content(client)
    result = client.post(
        f"/api/v1/content/{item['id']}/transition",
        headers=actor_headers(),
        json={"target_status": "IMPORTED", "reason": "source recorded"},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "IMPORTED"
    assert len(client.get(f"/api/v1/content/{item['id']}/audit").json()) == 2


def test_url_ingestion_requires_an_actor_before_url_processing(client):  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/ingestion/url", json={"url": "https://example.com/page"})
    assert response.status_code == 401


def test_multipart_upload_creates_safe_asset_response(client, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.ingestion.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(tmp_path)
    monkeypatch.setattr("app.ingestion.upload.LocalFilesystemStorage", lambda _: storage)
    response = client.post(
        "/api/v1/ingestion/upload",
        headers=actor_headers(),
        data={"idempotency_key": "multipart-upload-key"},
        files={"file": ("video.mp4", b"\x00\x00\x00\x18ftypisomapi", "video/mp4")},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "SUCCEEDED"
    assert payload["detected_media_type"] == "video/mp4"
    assert "storage_key" not in payload and "tmp_path" not in response.text
