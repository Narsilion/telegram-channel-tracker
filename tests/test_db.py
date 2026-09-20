import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telegram_channel_tracker.db import Database
from telegram_channel_tracker.schemas import RuleUpsert


def post_payload(message_id: int = 1) -> dict:
    return {
        "channel_id": 123,
        "telegram_message_id": message_id,
        "channel_title": "News",
        "channel_username": "news",
        "text": "Official product launch today",
        "posted_at": "2026-08-01T10:00:00+00:00",
        "post_url": f"https://t.me/news/{message_id}",
    }


def test_upsert_matching_and_delivery_are_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    rule = db.create_rule(RuleUpsert(name="Launch", include_terms=["product launch"]))
    post_id, created = db.upsert_post(post_payload())
    same_id, created_again = db.upsert_post(post_payload())
    assert created is True
    assert created_again is False
    assert same_id == post_id
    assert [item.name for item in db.sync_matches(post_id, post_payload()["text"])] == ["Launch"]
    assert db.claim_delivery(post_id, rule.id, "saved_messages") is True
    assert db.claim_delivery(post_id, rule.id, "saved_messages") is False
    db.finish_delivery(post_id, rule.id, "saved_messages")
    assert db.claim_delivery(post_id, rule.id, "saved_messages") is False


def test_failed_delivery_can_be_retried(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    rule = db.create_rule(RuleUpsert(name="Launch", include_terms=["launch"]))
    post_id, _ = db.upsert_post(post_payload())
    assert db.claim_delivery(post_id, rule.id, "saved_messages")
    db.finish_delivery(post_id, rule.id, "saved_messages", "offline")
    assert db.claim_delivery(post_id, rule.id, "saved_messages")


def test_new_rule_recomputes_archived_post_matches(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    post_id, _ = db.upsert_post(post_payload())
    assert db.get_post(post_id).matched_rule_names == []
    db.create_rule(RuleUpsert(name="Launch", include_terms=["launch"]))
    db.recompute_all_matches()
    assert db.get_post(post_id).matched_rule_names == ["Launch"]


def test_disabled_rule_does_not_match(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    post_id, _ = db.upsert_post(post_payload())
    db.create_rule(RuleUpsert(name="Launch", include_terms=["launch"], enabled=False))
    db.recompute_all_matches()
    assert db.get_post(post_id).matched_rule_names == []


def test_rule_notification_preferences_are_persisted(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    rule = db.create_rule(RuleUpsert(
        name="Launch", include_terms=["launch"],
        saved_messages_alerts=False, email_alerts=False, telegram_bot_alerts=True,
    ))
    assert rule.saved_messages_alerts is False
    assert rule.email_alerts is False
    assert rule.telegram_bot_alerts is True
    updated = db.update_rule(rule.id, RuleUpsert(
        name="Launch", include_terms=["launch"],
        email_alerts=True, telegram_bot_alerts=False,
    ))
    assert updated is not None
    assert updated.saved_messages_alerts is True
    assert updated.email_alerts is True
    assert updated.telegram_bot_alerts is False


def test_existing_rules_keep_all_notification_destinations_enabled(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            include_terms TEXT NOT NULL, match_mode TEXT NOT NULL,
            exclude_terms TEXT NOT NULL DEFAULT '[]', enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, target_id INTEGER
        )""")
        connection.execute(
            """INSERT INTO rules(
                name, include_terms, match_mode, exclude_terms, enabled,
                created_at, updated_at, target_id
            ) VALUES('Legacy', '[\"launch\"]', 'any', '[]', 1, 'now', 'now', 1)"""
        )
    db = Database(path)
    db.initialize()
    rule = db.list_rules()[0]
    assert rule.email_alerts is True
    assert rule.telegram_bot_alerts is True


def test_post_listing_can_be_scoped_to_topic(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    first = post_payload(1)
    first["topic_id"] = 21043
    second = post_payload(2)
    second["topic_id"] = 999
    db.upsert_post(first)
    db.upsert_post(second)
    assert [post.telegram_message_id for post in db.list_posts(topic_id=21043)] == [1]


def test_post_listing_can_be_limited_to_days_ago(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    recent = post_payload(1)
    recent["posted_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    old = post_payload(2)
    old["posted_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    db.upsert_post(recent)
    db.upsert_post(old)
    assert [post.telegram_message_id for post in db.list_posts(days=7)] == [1]


def test_delete_media_only_posts_preserves_captioned_media(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    blank = post_payload(1)
    blank["text"] = ""
    blank_id, _ = db.upsert_post(blank)
    db.replace_media(blank_id, [{"kind": "photo", "file_name": "blank.jpg", "local_path": "/tmp/blank.jpg", "status": "downloaded"}])
    captioned = post_payload(2)
    captioned_id, _ = db.upsert_post(captioned)
    db.replace_media(captioned_id, [{"kind": "photo", "file_name": "captioned.jpg", "status": "not_downloaded"}])
    deleted, paths = db.delete_media_only_posts()
    assert deleted == 1
    assert paths == ["/tmp/blank.jpg"]
    assert db.get_post(blank_id) is None
    assert db.get_post(captioned_id) is not None


def test_post_highlights_only_phrases_from_matching_rules(tmp_path: Path) -> None:
    db = Database(tmp_path / 'test.db')
    db.initialize()
    post_id, _ = db.upsert_post(post_payload())
    db.create_rule(RuleUpsert(name='Launch', include_terms=['launch', 'absent']))
    db.create_rule(RuleUpsert(name='Excluded', include_terms=['product'], exclude_terms=['today']))
    db.create_rule(RuleUpsert(name='Disabled', include_terms=['Official'], enabled=False))
    db.recompute_all_matches()
    post = db.get_post(post_id)
    assert [post.text[start:end] for start, end in post.matched_spans] == ['launch']


def test_saved_messages_migration_preserves_previous_preference(tmp_path: Path) -> None:
    db = Database(tmp_path / 'test.db')
    db.initialize()
    rule = db.create_rule(RuleUpsert(name='Launch', include_terms=['launch']))
    with db.connect() as connection:
        connection.execute('ALTER TABLE rules DROP COLUMN saved_messages_alerts')
    db.initialize(saved_messages_alerts=False)
    assert db.list_rules()[0].saved_messages_alerts is False
    db.initialize(saved_messages_alerts=True)
    assert db.list_rules()[0].saved_messages_alerts is False
