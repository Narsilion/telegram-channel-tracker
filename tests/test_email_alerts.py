import asyncio
import logging
from pathlib import Path

from telegram_channel_tracker.db import Database
from telegram_channel_tracker.email_alerts import GmailAlertSender
from telegram_channel_tracker.live import LiveBroker
from telegram_channel_tracker.schemas import RuleUpsert
from telegram_channel_tracker.settings import Settings
from telegram_channel_tracker.telegram_service import TelegramMonitor


class FakeEmailSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[tuple[str, str]] = []

    async def send(self, subject: str, body: str) -> None:
        if self.error:
            raise self.error
        self.messages.append((subject, body))


def build_monitor(tmp_path: Path, sender: FakeEmailSender) -> tuple[TelegramMonitor, Database, int, int]:
    db = Database(tmp_path / "tracker.db")
    db.initialize()
    rule = db.create_rule(RuleUpsert(name="Кокон", include_terms=["кокон"]))
    post_id, _ = db.upsert_post({
        "channel_id": 1, "telegram_message_id": 2, "channel_title": "Market",
        "text": "Новый кокон", "posted_at": "2026-08-02T00:00:00+00:00",
        "post_url": "https://t.me/c/1/2",
    })
    settings = Settings(
        data_dir=tmp_path, api_id=1, api_hash="hash", email_alerts=True,
        gmail_address="sender@gmail.com", email_recipient="recipient@example.com",
    )
    monitor = TelegramMonitor(
        settings, db, LiveBroker(), client=object(), email_sender=sender  # type: ignore[arg-type]
    )
    return monitor, db, post_id, rule.id


def test_email_alert_is_sent_once_per_post_and_rule(tmp_path: Path) -> None:
    sender = FakeEmailSender()
    monitor, _, post_id, rule_id = build_monitor(tmp_path, sender)
    payload = {
        "channel_title": "Market", "text": "Новый кокон",
        "post_url": "https://t.me/c/1/2",
    }
    asyncio.run(monitor._send_email_alert(post_id, rule_id, "Кокон", payload))
    asyncio.run(monitor._send_email_alert(post_id, rule_id, "Кокон", payload))
    assert len(sender.messages) == 1
    assert sender.messages[0][0] == "Telegram alert: Кокон — Market"
    assert "Open in Telegram: https://t.me/c/1/2" in sender.messages[0][1]


def test_failed_email_delivery_can_be_retried(tmp_path: Path) -> None:
    sender = FakeEmailSender(RuntimeError("offline"))
    monitor, db, post_id, rule_id = build_monitor(tmp_path, sender)
    payload = {"channel_title": "Market", "text": "кокон", "post_url": None}
    asyncio.run(monitor._send_email_alert(post_id, rule_id, "Кокон", payload))
    sender.error = None
    asyncio.run(monitor._send_email_alert(post_id, rule_id, "Кокон", payload))
    assert len(sender.messages) == 1
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM deliveries WHERE post_id=? AND rule_id=? AND destination='email'",
            (post_id, rule_id),
        ).fetchone()
    assert row["status"] == "delivered"


def test_successful_email_alert_is_logged(tmp_path: Path, caplog) -> None:
    sender = FakeEmailSender()
    monitor, _, post_id, rule_id = build_monitor(tmp_path, sender)
    payload = {
        "channel_title": "Market",
        "text": "Новый кокон",
        "post_url": "https://t.me/c/1/2",
    }
    caplog.set_level(logging.INFO, logger="telegram_channel_tracker.telegram_service")
    asyncio.run(monitor._send_email_alert(post_id, rule_id, "Кокон", payload))
    assert any(
        "Sent Gmail alert for rule Кокон on post" in record.message
        for record in caplog.records
    )


def test_gmail_sender_defaults_to_three_attempts(tmp_path: Path) -> None:
    sender = GmailAlertSender(Settings(data_dir=tmp_path))
    assert sender.attempts == 3

