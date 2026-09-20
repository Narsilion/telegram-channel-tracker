from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import subprocess
from dataclasses import replace
from email.message import EmailMessage

from telegram_channel_tracker.settings import Settings


KEYCHAIN_SERVICE = "telegram-channel-tracker.gmail"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465


class GmailConfigurationError(RuntimeError):
    pass


def gmail_is_configured(settings: Settings) -> bool:
    return bool(settings.gmail_address and settings.email_recipient)


def store_gmail_app_password(gmail_address: str, app_password: str) -> None:
    password = app_password.replace(" ", "").strip()
    if not password:
        raise GmailConfigurationError("Gmail App Password is required.")
    subprocess.run(
        [
            "/usr/bin/security", "add-generic-password", "-U",
            "-s", KEYCHAIN_SERVICE, "-a", gmail_address, "-w", password,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def load_gmail_app_password(settings: Settings) -> str:
    environment_password = os.environ.get("TCT_GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if environment_password and not settings.gmail_keychain_account:
        return environment_password
    if not settings.gmail_address:
        raise GmailConfigurationError("Gmail address is not configured.")
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-s", KEYCHAIN_SERVICE, "-a", settings.gmail_keychain_account or settings.gmail_address, "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise GmailConfigurationError(
            "Gmail App Password is missing from macOS Keychain; run `telegram-channel-tracker setup-email`."
        ) from exc
    password = result.stdout.strip()
    if not password:
        raise GmailConfigurationError("The Gmail App Password stored in macOS Keychain is empty.")
    return password


class GmailAlertSender:
    def __init__(self, settings: Settings, *, attempts: int = 3) -> None:
        self.settings = settings
        self.attempts = attempts

    async def send(self, subject: str, body: str, *, password: str | None = None) -> None:
        snapshot = replace(self.settings)
        sender = GmailAlertSender(snapshot, attempts=self.attempts)
        if not gmail_is_configured(snapshot):
            raise GmailConfigurationError("Gmail alerts are not configured.")
        if password is None:
            password = await asyncio.to_thread(load_gmail_app_password, snapshot)
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                await asyncio.to_thread(sender._send_once, subject, body, password)
                return
            except (OSError, smtplib.SMTPException) as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    await asyncio.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    def _send_once(self, subject: str, body: str, password: str) -> None:
        assert self.settings.gmail_address is not None
        assert self.settings.email_recipient is not None
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.gmail_address
        message["To"] = self.settings.email_recipient
        message.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, context=context, timeout=30
        ) as smtp:
            smtp.login(self.settings.gmail_address, password)
            smtp.send_message(message)

