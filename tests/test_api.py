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
        assert created.json()["email_alerts"] is True
        assert created.json()["telegram_bot_alerts"] is True
        assert client.get("/api/rules").json()[0]["name"] == "Launch"
        edited = client.put(f"/api/rules/{rule_id}", json={
            "name": "Edited launch", "include_terms": ["Люлька"],
            "exclude_terms": ["Люлька для коляски"], "match_mode": "any", "enabled": False,
            "email_alerts": False, "telegram_bot_alerts": True,
        })
        assert edited.status_code == 200
        assert edited.json()["name"] == "Edited launch"
        assert edited.json()["exclude_terms"] == ["Люлька для коляски"]
        assert edited.json()["enabled"] is False
        assert edited.json()["email_alerts"] is False
        assert edited.json()["telegram_bot_alerts"] is True
        updated = client.put("/api/settings", json={
            "channel_ref": "@new", "backfill_limit": 100, "media_max_mb": 25,
            "media_retention_days": 30, "saved_messages_alerts": True,
        })
        assert updated.status_code == 200
        assert "saved_messages_alerts" not in updated.json()
        assert updated.json()["download_media"] is False


def test_health_endpoint(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_posts_days_filter_is_validated(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, channel_ref="123")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/posts?days=7").status_code == 200
        assert client.get("/api/posts?days=0").status_code == 422
        assert client.get("/api/targets/1/posts?days=3651").status_code == 422


def test_favicon_is_served(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/favicon.svg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/svg+xml"
        assert response.text.startswith("<svg")


def test_preferences_report_account_setup_without_global_switches(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        preferences = client.get('/api/preferences').json()
        assert preferences['email_configured'] is False
        assert preferences['telegram_bot_configured'] is False
        for key in ('email_alerts', 'telegram_bot_alerts', 'saved_messages_alerts'):
            assert key not in preferences
        settings.gmail_address = 'sender@gmail.com'
        settings.email_recipient = 'recipient@example.com'
        settings.telegram_bot_username = 'tracker_alert_bot'
        settings.telegram_bot_chat_id = 123
        preferences = client.get('/api/preferences').json()
        assert preferences['email_configured'] is True
        assert preferences['telegram_bot_configured'] is True


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


def test_posts_exclusions_apply_before_pagination_and_preserve_archive(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, channel_ref="123"))
    db = app.state.db
    target = db.list_targets()[0]
    texts = ["offer first", "offer second", "offer СПАМ", "offer Cafe\u0301", "offer 100%", "offer sold OUT"]
    for message_id, text in enumerate(texts, 1):
        db.upsert_post({
            "channel_id": target.channel_id, "telegram_message_id": message_id,
            "channel_title": "Test", "text": text, "posted_at": f"2026-08-{message_id:02}T00:00:00+00:00",
        })
    with TestClient(app) as client:
        for url in ("/api/posts", f"/api/targets/{target.id}/posts"):
            response = client.get(url, params={
                "q": "offer", "exclude_terms": [" спам ", "CAFÉ", "100%", "sold out", " "],
                "limit": 1, "offset": 1, "matched": "false",
            })
            assert response.status_code == 200
            assert [p["text"] for p in response.json()] == ["offer first"]
            assert len(client.get(url).json()) == len(texts)
            assert len(client.get(url, params={"exclude_terms": "_"}).json()) == len(texts)
