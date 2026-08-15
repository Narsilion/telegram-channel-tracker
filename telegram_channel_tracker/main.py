from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os

import uvicorn
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from telegram_channel_tracker.app import create_app
from telegram_channel_tracker.bot_alerts import TelegramBotAlertSender, call_bot_api, store_bot_token
from telegram_channel_tracker.db import Database
from telegram_channel_tracker.email_alerts import GmailAlertSender, store_gmail_app_password
from telegram_channel_tracker.settings import Settings, load_settings, save_settings
from telegram_channel_tracker.telegram_refs import is_trackable_channel, parse_channel_target, resolve_channel, resolve_topic_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-channel-tracker")
    sub = parser.add_subparsers(dest="command")
    setup = sub.add_parser("setup", help="Authorize your personal Telegram account and choose a channel.")
    setup.add_argument("--api-id", type=int)
    setup.add_argument("--api-hash")
    setup.add_argument("--channel")
    email_setup = sub.add_parser("setup-email", help="Configure Gmail alerts using a macOS Keychain App Password.")
    email_setup.add_argument("--gmail")
    email_setup.add_argument("--recipient")
    sub.add_parser("setup-bot", help="Configure incoming Telegram alerts through a private bot.")
    run = sub.add_parser("run", help="Run the local dashboard and tracker.")
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "setup":
        asyncio.run(run_setup(args))
        return
    if args.command == "setup-email":
        asyncio.run(run_email_setup(args))
        return
    if args.command == "setup-bot":
        asyncio.run(run_bot_setup())
        return
    if args.command in (None, "run"):
        settings = load_settings()
        if getattr(args, "host", None):
            settings.host = args.host
        if getattr(args, "port", None):
            settings.port = args.port
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


async def run_setup(args: argparse.Namespace) -> None:
    settings = load_settings()
    api_id = args.api_id or settings.api_id or _prompt_int("Telegram api_id: ")
    api_hash = args.api_hash or settings.api_hash or getpass.getpass("Telegram api_hash (hidden): ").strip()
    if not api_hash:
        raise SystemExit("api_hash is required.")
    settings.api_id = api_id
    settings.api_hash = api_hash
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(settings.session_path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            phone = input("Personal-account phone number (international format): ").strip()
            sent = await client.send_code_request(phone)
            code = getpass.getpass("Telegram login code (hidden): ").strip()
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password (hidden): ")
                await client.sign_in(password=password)
        me = await client.get_me()
        save_settings(settings)
        requested = args.channel or input("Channel @username, link, or numeric ID: ").strip()
        _, linked_message_id = parse_channel_target(requested)
        try:
            entity = await resolve_channel(client, requested)
        except ValueError as exc:
            raise SystemExit(f"Could not resolve channel: {exc}") from exc
        if not is_trackable_channel(entity):
            raise SystemExit("The selected chat is neither a Telegram channel nor a supergroup.")
        previous_target = (settings.channel_ref, settings.topic_id)
        settings.channel_ref = f"@{entity.username}" if getattr(entity, "username", None) else str(entity.id)
        settings.topic_id = await resolve_topic_id(client, entity, linked_message_id)
        save_settings(settings)
        if previous_target != (settings.channel_ref, settings.topic_id):
            database = Database(settings.db_path)
            database.initialize()
            database.delete_state("last_message_id")
            database.delete_state("backfill_target")
        topic_label = f" (topic {settings.topic_id})" if settings.topic_id else ""
        print(f"Authorized as {getattr(me, 'first_name', '')} and configured channel: {entity.title}{topic_label}")
        print(f"Run `telegram-channel-tracker run`, then open http://{settings.host}:{settings.port}")
    finally:
        await client.disconnect()
        session_file = settings.session_path.with_suffix(".session")
        if session_file.exists():
            os.chmod(session_file, 0o600)


async def run_email_setup(args: argparse.Namespace) -> None:
    settings = load_settings()
    gmail_address = (args.gmail or settings.gmail_address or input("Gmail address: ")).strip()
    if "@" not in gmail_address:
        raise SystemExit("Enter a valid Gmail address.")
    recipient = (args.recipient or settings.email_recipient or input(f"Alert recipient [{gmail_address}]: ").strip() or gmail_address).strip()
    if "@" not in recipient:
        raise SystemExit("Enter a valid recipient email address.")
    app_password = getpass.getpass("Gmail App Password (hidden, spaces are optional): ")
    store_gmail_app_password(gmail_address, app_password)
    settings.gmail_address = gmail_address
    settings.email_recipient = recipient
    settings.email_alerts = False
    save_settings(settings)
    print(f"Sending a test email to {recipient}...")
    try:
        await GmailAlertSender(settings).send(
            "Telegram Channel Tracker test",
            "Gmail alerts are configured. New live rule matches can now be emailed to this address.",
        )
    except Exception as exc:
        raise SystemExit(f"Test email failed; email alerts remain disabled: {exc}") from exc
    settings.email_alerts = True
    save_settings(settings)
    print(f"Test email sent. Email alerts are enabled for {recipient}.")


async def run_bot_setup() -> None:
    settings = load_settings()
    token = getpass.getpass("BotFather token (hidden): ").strip()
    try:
        bot = await asyncio.to_thread(call_bot_api, token, "getMe")
    except Exception as exc:
        raise SystemExit(f"Could not validate bot token: {exc}") from exc
    if not isinstance(bot, dict) or not bot.get("username"):
        raise SystemExit("Telegram did not return a bot username.")
    username = str(bot["username"])
    print(f"Open https://t.me/{username} from the Telegram account that should receive alerts and press Start.")
    input("Press Enter here after you have started the bot...")
    try:
        updates = await asyncio.to_thread(
            call_bot_api, token, "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}
        )
    except Exception as exc:
        raise SystemExit(f"Could not read the bot conversation: {exc}") from exc
    private_messages = [
        update["message"] for update in (updates or [])
        if isinstance(update, dict)
        and isinstance(update.get("message"), dict)
        and update["message"].get("chat", {}).get("type") == "private"
    ]
    if not private_messages:
        raise SystemExit(f"No private conversation found. Open https://t.me/{username}, press Start, and run setup-bot again.")
    chat_id = int(private_messages[-1]["chat"]["id"])
    settings.telegram_bot_username = username
    settings.telegram_bot_chat_id = chat_id
    settings.telegram_bot_alerts = False
    save_settings(settings)
    store_bot_token(username, token)
    print("Sending a test Telegram alert...")
    try:
        await TelegramBotAlertSender(settings).send(
            "🔔 Telegram Channel Tracker test\nIncoming bot alerts are configured."
        )
    except Exception as exc:
        raise SystemExit(f"Test bot alert failed; bot alerts remain disabled: {exc}") from exc
    settings.telegram_bot_alerts = True
    save_settings(settings)
    print(f"Test alert sent by @{username}. Bot alerts are enabled; restart Telegram Channel Tracker.")


def _prompt_int(prompt: str) -> int:
    try:
        return int(input(prompt).strip())
    except ValueError as exc:
        raise SystemExit("api_id must be an integer.") from exc


if __name__ == "__main__":
    main()
