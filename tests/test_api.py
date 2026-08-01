from pathlib import Path

from fastapi.testclient import TestClient

from telegram_channel_tracker.app import create_app
from telegram_channel_tracker.settings import Settings


def test_rules_and_settings_api(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, channel_ref="@old")
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/rules", json={"name": "Launch", "include_terms": ["launch"], "match_mode": "any"})
        assert created.status_code == 201
        rule_id = created.json()["id"]
        assert client.get("/api/rules").json()[0]["name"] == "Launch"
        edited = client.put(f"/api/rules/{rule_id}", json={
            "name": "Edited launch", "include_terms": ["Люлька"],
            "exclude_terms": ["Люлька для коляски"], "match_mode": "any", "enabled": False,
        })
        assert edited.status_code == 200
        assert edited.json()["name"] == "Edited launch"
        assert edited.json()["exclude_terms"] == ["Люлька для коляски"]
        assert edited.json()["enabled"] is False
        updated = client.put("/api/settings", json={
            "channel_ref": "@new", "backfill_limit": 100, "media_max_mb": 25,
            "media_retention_days": 30, "saved_messages_alerts": True,
        })
        assert updated.status_code == 200
        assert updated.json()["saved_messages_alerts"] is True
        assert updated.json()["download_media"] is False


def test_health_endpoint(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_private_message_link_configures_topic(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, channel_ref="1163031069")
    with TestClient(create_app(settings)) as client:
        response = client.put("/api/settings", json={
            "channel_ref": "https://t.me/c/1163031069/21043", "backfill_limit": 100,
            "media_max_mb": 25, "media_retention_days": 30, "saved_messages_alerts": True,
        })
        assert response.status_code == 200
        assert response.json()["channel_ref"] == "https://t.me/c/1163031069/21043"
        assert settings.topic_id == 21043


def test_media_path_is_not_exposed(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    post_id, _ = app.state.db.upsert_post({
        "channel_id": 1, "telegram_message_id": 1, "channel_title": "Test", "text": "x",
        "posted_at": "2026-08-01T00:00:00+00:00",
    })
    app.state.db.replace_media(post_id, [{"kind": "document", "file_name": "secret", "local_path": "/etc/passwd", "status": "downloaded"}])
    with TestClient(app) as client:
        assert client.get(f"/api/posts/{post_id}/media/0").status_code == 404
