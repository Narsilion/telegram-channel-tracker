import asyncio
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from telegram_channel_tracker.bot_alerts import TelegramBotError, call_bot_api
from telegram_channel_tracker.db import Database
from telegram_channel_tracker.live import LiveBroker
from telegram_channel_tracker.schemas import RuleUpsert
from telegram_channel_tracker.settings import Settings
from telegram_channel_tracker.telegram_service import TelegramMonitor


class FakeBotSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        if self.error:
            raise self.error
        self.messages.append(text)


def build_monitor(tmp_path: Path, sender: FakeBotSender) -> tuple[TelegramMonitor, Database, int, int]:
    db = Database(tmp_path / "tracker.db")
    db.initialize()
    rule = db.create_rule(RuleUpsert(name="Кокон", include_terms=["кокон"]))
    post_id, _ = db.upsert_post({
        "channel_id": 1, "telegram_message_id": 2, "channel_title": "Market",
        "text": "Новый кокон", "posted_at": "2026-08-02T00:00:00+00:00",
        "post_url": "https://t.me/c/1/2",
    })
    settings = Settings(
        data_dir=tmp_path, api_id=1, api_hash="hash", telegram_bot_alerts=True,
        telegram_bot_username="tracker_alert_bot", telegram_bot_chat_id=123,
    )
    monitor = TelegramMonitor(
        settings, db, LiveBroker(), client=object(), bot_sender=sender  # type: ignore[arg-type]
    )
    return monitor, db, post_id, rule.id


def test_bot_alert_is_sent_once_per_post_and_rule(tmp_path: Path) -> None:
    sender = FakeBotSender()
    monitor, _, post_id, rule_id = build_monitor(tmp_path, sender)
    payload = {
        "channel_title": "Market", "text": "Новый кокон",
        "post_url": "https://t.me/c/1/2",
    }
    asyncio.run(monitor._send_bot_alert(post_id, rule_id, "Кокон", payload))
    asyncio.run(monitor._send_bot_alert(post_id, rule_id, "Кокон", payload))
    assert len(sender.messages) == 1
    assert "Rule: Кокон" in sender.messages[0]
    assert "https://t.me/c/1/2" in sender.messages[0]


def test_failed_bot_delivery_can_be_retried(tmp_path: Path) -> None:
    sender = FakeBotSender(RuntimeError("offline"))
    monitor, db, post_id, rule_id = build_monitor(tmp_path, sender)
    payload = {"channel_title": "Market", "text": "кокон", "post_url": None}
    asyncio.run(monitor._send_bot_alert(post_id, rule_id, "Кокон", payload))
    sender.error = None
    asyncio.run(monitor._send_bot_alert(post_id, rule_id, "Кокон", payload))
    assert len(sender.messages) == 1
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM deliveries WHERE post_id=? AND rule_id=? AND destination='telegram_bot'",
            (post_id, rule_id),
        ).fetchone()
    assert row["status"] == "delivered"


def test_bot_network_error_does_not_expose_token() -> None:
    with patch("telegram_channel_tracker.bot_alerts.urlopen", side_effect=URLError("offline")):
        with pytest.raises(TelegramBotError) as caught:
            call_bot_api("secret-token", "getMe")
    assert "secret-token" not in str(caught.value)

