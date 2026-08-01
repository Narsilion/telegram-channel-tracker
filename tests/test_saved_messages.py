import asyncio
from pathlib import Path

from telegram_channel_tracker.db import Database
from telegram_channel_tracker.live import LiveBroker
from telegram_channel_tracker.schemas import RuleUpsert
from telegram_channel_tracker.settings import Settings
from telegram_channel_tracker.telegram_service import TelegramMonitor


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, bool]] = []

    async def send_message(self, destination: str, text: str, *, link_preview: bool) -> None:
        self.messages.append((destination, text, link_preview))


def test_saved_messages_alert_is_sent_once_per_post_and_rule(tmp_path: Path) -> None:
    db = Database(tmp_path / "tracker.db")
    db.initialize()
    rule = db.create_rule(RuleUpsert(name="Launch", include_terms=["launch"]))
    post_id, _ = db.upsert_post({
        "channel_id": 1, "telegram_message_id": 2, "channel_title": "News",
        "text": "Launch today", "posted_at": "2026-08-01T00:00:00+00:00",
        "post_url": "https://t.me/news/2",
    })
    client = FakeClient()
    monitor = TelegramMonitor(
        Settings(data_dir=tmp_path, api_id=1, api_hash="hash", channel_ref="@news"),
        db, LiveBroker(), client=client,  # type: ignore[arg-type]
    )
    payload = {"channel_title": "News", "text": "Launch today", "post_url": "https://t.me/news/2"}
    asyncio.run(monitor._send_saved_alert(post_id, rule.id, rule.name, payload))
    asyncio.run(monitor._send_saved_alert(post_id, rule.id, rule.name, payload))
    assert len(client.messages) == 1
    assert client.messages[0][0] == "me"
    assert "Rule: Launch" in client.messages[0][1]
    assert "https://t.me/news/2" in client.messages[0][1]

