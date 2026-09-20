from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from telegram_channel_tracker.settings import Settings


KEYCHAIN_SERVICE = "telegram-channel-tracker.bot"


class TelegramBotError(RuntimeError):
    pass


def bot_is_configured(settings: Settings) -> bool:
    return bool(settings.telegram_bot_username and settings.telegram_bot_chat_id)


def store_bot_token(bot_username: str, token: str) -> None:
    cleaned = token.strip()
    if not cleaned:
        raise TelegramBotError("Bot token is required.")
    subprocess.run(
        [
            "/usr/bin/security", "add-generic-password", "-U",
            "-s", KEYCHAIN_SERVICE, "-a", bot_username, "-w", cleaned,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def load_bot_token(settings: Settings) -> str:
    environment_token = os.environ.get("TCT_TELEGRAM_BOT_TOKEN", "").strip()
    if environment_token and not settings.bot_keychain_account:
        return environment_token
    if not settings.telegram_bot_username:
        raise TelegramBotError("Telegram alert bot is not configured.")
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-s", KEYCHAIN_SERVICE, "-a", settings.bot_keychain_account or settings.telegram_bot_username, "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise TelegramBotError(
            "Bot token is missing from macOS Keychain; run `telegram-channel-tracker setup-bot`."
        ) from exc
    token = result.stdout.strip()
    if not token:
        raise TelegramBotError("The bot token stored in macOS Keychain is empty.")
    return token


def call_bot_api(token: str, method: str, payload: dict | None = None) -> object:
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=35) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("description")
        except Exception:
            detail = None
        raise TelegramBotError(detail or f"Telegram Bot API returned HTTP {exc.code}.") from exc
    except (URLError, OSError) as exc:
        raise TelegramBotError("Could not connect to the Telegram Bot API.") from exc
    if not raw.get("ok"):
        raise TelegramBotError(str(raw.get("description") or "Telegram Bot API request failed."))
    return raw.get("result")


class TelegramBotAlertSender:
    def __init__(self, settings: Settings, *, attempts: int = 3) -> None:
        self.settings = settings
        self.attempts = attempts

    async def send(self, text: str) -> None:
        snapshot = replace(self.settings)
        if not bot_is_configured(snapshot):
            raise TelegramBotError("Telegram alert bot is not configured.")
        token = await asyncio.to_thread(load_bot_token, snapshot)
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                await asyncio.to_thread(
                    call_bot_api,
                    token,
                    "sendMessage",
                    {
                        "chat_id": snapshot.telegram_bot_chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
                return
            except TelegramBotError as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    await asyncio.sleep(2**attempt)
        assert last_error is not None
        raise last_error

