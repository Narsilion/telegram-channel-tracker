import asyncio

import pytest
from unittest.mock import AsyncMock
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


@pytest.mark.parametrize("configured", [True, False])
def test_live_alert_destinations_are_selected_per_rule(tmp_path: Path, configured: bool) -> None:
    db = Database(tmp_path / "tracker.db")
    db.initialize()
    target = db.create_target(
        channel_ref="@news", channel_id=100, topic_id=None, title="News",
    )
    db.create_rule(target.id, RuleUpsert(
        name="Email only", include_terms=["launch"],
        saved_messages_alerts=False, email_alerts=True, telegram_bot_alerts=False,
    ))
    db.create_rule(target.id, RuleUpsert(
        name="Telegram only", include_terms=["launch"],
        saved_messages_alerts=False, email_alerts=False, telegram_bot_alerts=True,
    ))
    db.create_rule(target.id, RuleUpsert(
        name="Saved only", include_terms=["launch"],
        saved_messages_alerts=True, email_alerts=False, telegram_bot_alerts=False,
    ))
    telegram_client = SimpleNamespace(send_message=AsyncMock())
    email_sender = FakeEmailSender()
    bot_sender = FakeBotSender()
    monitor = TelegramMonitor(
        Settings(
            data_dir=tmp_path, api_id=1, api_hash="hash",
            saved_messages_alerts=False, email_alerts=False,
            telegram_bot_alerts=False,
            gmail_address="sender@gmail.com" if configured else None,
            email_recipient="recipient@example.com" if configured else None,
            telegram_bot_username="tracker_bot" if configured else None,
            telegram_bot_chat_id=123 if configured else None,
        ),
        db,
        LiveBroker(),
        client=telegram_client,  # type: ignore[arg-type]
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

    assert len(email_sender.messages) == int(configured)
    assert len(bot_sender.messages) == int(configured)
    if configured:
        assert "Email only" in email_sender.messages[0][0]
        assert "Rule: Telegram only" in bot_sender.messages[0]
    assert telegram_client.send_message.await_count == 1
    assert "Saved only" in telegram_client.send_message.call_args.args[1]

    message.id += 1
    asyncio.run(monitor._ingest(target, entity, message, live=False))
    assert len(email_sender.messages) == int(configured)
    assert len(bot_sender.messages) == int(configured)
    assert telegram_client.send_message.await_count == 1
