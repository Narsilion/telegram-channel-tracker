import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from telegram_channel_tracker.db import Database
from telegram_channel_tracker.live import LiveBroker
from telegram_channel_tracker.schemas import RuleUpsert
from telegram_channel_tracker.settings import Settings
from telegram_channel_tracker.telegram_service import TelegramMonitor


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def send(self, subject: str, body: str) -> None:
        self.messages.append((subject, body))


class FakeBotSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)


def test_live_alert_destinations_are_selected_per_rule(tmp_path: Path) -> None:
    db = Database(tmp_path / "tracker.db")
    db.initialize()
    target = db.create_target(
        channel_ref="@news", channel_id=100, topic_id=None, title="News",
    )
    db.create_rule(target.id, RuleUpsert(
        name="Email only", include_terms=["launch"],
        email_alerts=True, telegram_bot_alerts=False,
    ))
    db.create_rule(target.id, RuleUpsert(
        name="Telegram only", include_terms=["launch"],
        email_alerts=False, telegram_bot_alerts=True,
    ))
    email_sender = FakeEmailSender()
    bot_sender = FakeBotSender()
    monitor = TelegramMonitor(
        Settings(
            data_dir=tmp_path, api_id=1, api_hash="hash",
            saved_messages_alerts=False, email_alerts=True,
            telegram_bot_alerts=True,
        ),
        db,
        LiveBroker(),
        client=object(),  # type: ignore[arg-type]
        email_sender=email_sender,  # type: ignore[arg-type]
        bot_sender=bot_sender,  # type: ignore[arg-type]
    )
    entity = SimpleNamespace(id=100, username="news", title="News")
    message = SimpleNamespace(
        id=5, message="Product launch today", media=None,
        date=datetime(2026, 8, 31, tzinfo=UTC), edit_date=None,
        views=10, forwards=1, grouped_id=None,
    )

    asyncio.run(monitor._ingest(target, entity, message, live=True))

    assert len(email_sender.messages) == 1
    assert "Email only" in email_sender.messages[0][0]
    assert len(bot_sender.messages) == 1
    assert "Rule: Telegram only" in bot_sender.messages[0]
