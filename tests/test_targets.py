from pathlib import Path

from fastapi.testclient import TestClient

from telegram_channel_tracker.app import create_app
from telegram_channel_tracker.db import Database
from telegram_channel_tracker.schemas import RuleUpsert
from telegram_channel_tracker.settings import Settings


def test_targets_scope_rules_posts_and_stats(tmp_path: Path) -> None:
    db = Database(tmp_path / "tracker.db")
    db.initialize()
    first = db.create_target(channel_ref="100", channel_id=100, topic_id=10, title="First", backfill_limit=50)
    second = db.create_target(channel_ref="200", channel_id=200, topic_id=None, title="Second", backfill_limit=25)
    db.create_rule(first.id, RuleUpsert(name="First rule", include_terms=["match"]))
    db.create_rule(second.id, RuleUpsert(name="Second rule", include_terms=["other"]))
    post_id, _ = db.upsert_post({
        "channel_id": 100, "telegram_message_id": 1, "topic_id": 10,
        "channel_title": "First", "text": "a match", "posted_at": "2026-08-01T00:00:00+00:00",
    })
    db.sync_matches(first.id, post_id, "a match")
    assert [rule.name for rule in db.list_rules(first.id)] == ["First rule"]
    assert [rule.name for rule in db.list_rules(second.id)] == ["Second rule"]
    assert db.target_stats(first)["matched_count"] == 1
    assert len(db.list_posts(channel_id=100, topic_id=10, target_id=first.id)) == 1
    assert db.list_posts(channel_id=200, target_id=second.id) == []


def test_card_index_and_target_detail_are_available(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, channel_ref="1163031069", topic_id=21043)
    app = create_app(settings)
    target = app.state.db.list_targets()[0]
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Tracked channels and topics" in home.text
        detail = client.get(f"/channels/{target.id}")
        assert detail.status_code == 200
        assert f"const targetId={target.id}" in detail.text
        cards = client.get("/api/targets").json()
        assert cards[0]["topic_id"] == 21043


def test_increasing_target_limit_requests_a_fresh_backfill(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, channel_ref="1163031069", topic_id=21043)
    app = create_app(settings)
    target = app.state.db.list_targets()[0]
    app.state.db.set_target_state(target.id, "backfill_target", "1163031069:21043")
    app.state.db.set_target_state(target.id, "last_message_id", "900")

    with TestClient(app) as client:
        response = client.put(
            f"/api/targets/{target.id}",
            json={"enabled": True, "backfill_limit": 200},
        )

    assert response.status_code == 200
    assert app.state.db.get_target_state(target.id, "backfill_target") is None
    assert app.state.db.get_target_state(target.id, "last_message_id") == "900"


def test_unchanged_target_limit_preserves_completed_backfill(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, channel_ref="1163031069", topic_id=21043)
    app = create_app(settings)
    target = app.state.db.list_targets()[0]
    marker = "1163031069:21043"
    app.state.db.set_target_state(target.id, "backfill_target", marker)

    with TestClient(app) as client:
        response = client.put(
            f"/api/targets/{target.id}",
            json={"enabled": True, "backfill_limit": target.backfill_limit},
        )

    assert response.status_code == 200
    assert app.state.db.get_target_state(target.id, "backfill_target") == marker
