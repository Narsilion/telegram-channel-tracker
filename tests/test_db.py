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
