from __future__ import annotations

import asyncio
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, replace

from telegram_channel_tracker.bot_alerts import (
    KEYCHAIN_SERVICE as BOT_SERVICE, bot_is_configured,
    call_bot_api, load_bot_token, store_bot_token,
)
from telegram_channel_tracker.email_alerts import (
    KEYCHAIN_SERVICE as EMAIL_SERVICE, GmailAlertSender, gmail_is_configured,
    load_gmail_app_password, store_gmail_app_password,
)
from telegram_channel_tracker.settings import Settings, save_settings


class SetupError(ValueError):
    """A user-facing error that never includes credentials or provider exceptions."""


@dataclass(repr=False)
class BotConnection:
    token: str
    username: str
    code: str
    expires: float


class NotificationSetup:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connections: dict[str, BotConnection] = {}
        self.lock = asyncio.Lock()

    def status(self) -> dict:
        return {
            'email_configured': gmail_is_configured(self.settings),
            'gmail_address': self.settings.gmail_address,
            'email_recipient': self.settings.email_recipient,
            'telegram_bot_configured': bot_is_configured(self.settings),
            'telegram_bot_username': self.settings.telegram_bot_username,
            'telegram_bot_chat_id': self.settings.telegram_bot_chat_id,
        }

    @staticmethod
    def address(value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value) or len(value) > 254:
            raise SetupError('Enter a valid sender and recipient email address.')
        return value

    async def save_email(self, sender: str, recipient: str, password: str) -> dict:
        sender = self.address(sender)
        recipient = self.address(recipient or sender)
        password = password.replace(' ', '').strip()
        if not password:
            if sender != self.settings.gmail_address:
                raise SetupError('Enter an App Password for this Gmail sender.')
            try:
                password = await asyncio.to_thread(load_gmail_app_password, self.settings)
            except Exception:
                raise SetupError('Stored App Password unavailable. Enter it again.') from None
        candidate = replace(self.settings, gmail_address=sender, email_recipient=recipient)
        await self._email_test(candidate, password)
        await self._commit('email', password, gmail_address=sender, email_recipient=recipient)
        return self.status()

    async def _email_test(self, settings: Settings, password: str | None = None) -> None:
        try:
            await GmailAlertSender(settings).send(
                'Telegram Channel Tracker test',
                'Email delivery works. Select Email in each rule to receive live alerts.',
                password=password,
            )
        except Exception:
            raise SetupError('Test email failed. Check the App Password, sender, recipient, and internet connection.') from None

    async def test_email(self) -> None:
        await self._email_test(self.settings)

    async def begin_bot(self, token: str) -> dict:
        token = token.strip()
        if not token or len(token) > 512:
            raise SetupError('Enter the bot token from BotFather.')
        try:
            bot = await asyncio.to_thread(call_bot_api, token, 'getMe')
            if not isinstance(bot, dict) or not bot.get('is_bot') or not bot.get('username'):
                raise ValueError()
            webhook = await asyncio.to_thread(call_bot_api, token, 'getWebhookInfo')
            if isinstance(webhook, dict) and webhook.get('url'):
                raise SetupError('This bot uses a webhook in another application. Create a dedicated alert bot with BotFather.')
        except SetupError:
            raise
        except Exception:
            raise SetupError('Could not validate the bot. Check its token and your internet connection.') from None
        self.connections = {k: v for k, v in self.connections.items() if v.expires > time.monotonic()}
        if len(self.connections) >= 10:
            raise SetupError('Too many pending connections. Wait ten minutes and try again.')
        connection_id = secrets.token_urlsafe(24)
        code = secrets.token_urlsafe(24)
        username = str(bot['username'])
        self.connections[connection_id] = BotConnection(token, username, code, time.monotonic() + 600)
        asyncio.get_running_loop().call_later(600, self.connections.pop, connection_id, None)
        return {'connection_id': connection_id, 'username': username,
                'url': f'https://t.me/{username}?start={code}'}

    async def finish_bot(self, connection_id: str) -> dict:
        connection = self.connections.get(connection_id)
        if connection is None or connection.expires <= time.monotonic():
            self.connections.pop(connection_id, None)
            raise SetupError('Connection expired. Connect the bot again.')
        try:
            updates = []
            offset = 0
            for _ in range(20):
                batch = await asyncio.to_thread(call_bot_api, connection.token, 'getUpdates',
                    {'timeout': 0, 'offset': offset, 'allowed_updates': ['message']})
                updates.extend(batch or [])
                if not batch or len(batch) < 100:
                    break
                offset = max(update['update_id'] for update in batch) + 1
            else:
                raise SetupError('Too many pending bot messages. Press the connection link again and retry.')
        except SetupError:
            raise
        except Exception:
            raise SetupError('Could not read the bot conversation. Check your connection and ensure no other app is polling this bot.') from None
        chats = {
            int(message['chat']['id'])
            for update in (updates or []) if isinstance(update, dict)
            for message in [update.get('message')] if isinstance(message, dict)
            if message.get('chat', {}).get('type') == 'private'
            and message.get('text', '').strip() == f'/start {connection.code}'
        }
        if not chats:
            raise SetupError('Open the connection link and press Start in Telegram, then try again.')
        if len(chats) != 1:
            raise SetupError('Multiple recipients used this link. Connect again with a new link.')
        chat_id = chats.pop()
        await self._bot_test(connection.token, chat_id)
        await self._commit('bot', connection.token, telegram_bot_username=connection.username,
                           telegram_bot_chat_id=chat_id)
        self.connections.pop(connection_id, None)
        return self.status()

    async def _bot_test(self, token: str, chat_id: int) -> None:
        try:
            await asyncio.to_thread(call_bot_api, token, 'sendMessage', {
                'chat_id': chat_id,
                'text': 'Telegram Channel Tracker test\nBot delivery works. Select Telegram in each rule to receive live alerts.',
            })
        except Exception:
            raise SetupError('Test Telegram alert failed. Make sure the bot is started and not blocked, and check your connection.') from None

    async def test_bot(self) -> None:
        try:
            token = await asyncio.to_thread(load_bot_token, self.settings)
        except Exception:
            raise SetupError('Stored bot token unavailable. Connect the bot again.') from None
        if not self.settings.telegram_bot_chat_id:
            raise SetupError('Connect a Telegram recipient first.')
        await self._bot_test(token, self.settings.telegram_bot_chat_id)

    async def _commit(self, kind: str, secret: str, **changes: object) -> None:
        # Stage a new Keychain item so failed config writes cannot replace working credentials.
        account = f'tracker-{secrets.token_hex(16)}'
        field = 'gmail_keychain_account' if kind == 'email' else 'bot_keychain_account'
        service = EMAIL_SERVICE if kind == 'email' else BOT_SERVICE
        store = store_gmail_app_password if kind == 'email' else store_bot_token
        try:
            await asyncio.to_thread(store, account, secret)
            candidate = replace(self.settings, **changes, **{field: account})
            save_settings(candidate)
        except Exception:
            await self._delete_staged(service, account)
            raise SetupError('Could not save setup. Check macOS Keychain access and configuration file permissions. Previous settings are unchanged.') from None
        # All users of the shared Settings instance see the successful setup immediately.
        old_account = getattr(self.settings, field)
        for key, value in {**changes, field: account}.items():
            setattr(self.settings, key, value)
        if old_account and old_account.startswith('tracker-'):
            await self._delete_staged(service, old_account)


    @staticmethod
    async def _delete_staged(service: str, account: str) -> None:
        try:
            await asyncio.to_thread(subprocess.run, ['/usr/bin/security', 'delete-generic-password',
                '-s', service, '-a', account], capture_output=True, check=False)
        except OSError:
            pass  # A cleanup failure must not turn a successful commit into a reported failure.
