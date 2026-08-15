# Telegram Channel Tracker

A local web dashboard that uses your personal Telegram account to track multiple channels or supergroup topics, match target-specific keyword rules, show browser notifications, and send matching alerts to **Saved Messages** or email.

The tracker is read-only toward the configured channel. It does not join channels, post, react, forward, or perform moderation actions. The only outbound Telegram action is sending configured alerts to your own Saved Messages.

## Requirements

- Python 3.12+
- A Telegram account with access to the channel or supergroup
- Your own `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org) → **API development tools**

## Install and authorize

```bash
cd /Users/darkcreation/Documents/git_repos/telegram-channel-tracker
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
telegram-channel-tracker setup
```

Setup asks for your phone number, the one-time code Telegram sends, and your 2FA password if enabled. Codes and passwords are never saved. Configuration and the reusable Telethon session are stored under `.data/` with owner-only permissions.

Treat `.data/telegram.session` like a password: anyone who obtains it may be able to use your Telegram account. Revoke the session from Telegram's official client if it is ever exposed.

## Run

```bash
.venv/bin/telegram-channel-tracker run
```

Open <http://127.0.0.1:8775>. The main page shows one card per tracked channel or topic. Add targets by username or Telegram link, then open a card to manage its rules and archive. A live match is sent to Saved Messages once per matching rule and shown as a browser notification when the dashboard is open and notification permission is granted.

## Gmail alerts

Gmail alerts require a Google App Password. Turn on 2-Step Verification for the sending Google account, create an App Password, then run:

```bash
.venv/bin/telegram-channel-tracker setup-email
```

Enter the Gmail sender, recipient, and 16-character App Password when prompted. The password is stored in macOS Keychain and is never written to `config.json`. Setup sends a test email and enables email alerts only when that test succeeds. Email delivery uses Gmail SMTP over SSL and retries transient delivery failures three times.

The Mac must remain awake, online, and running the tracker to detect Telegram messages and send alerts. If the Mac sleeps or shuts down, real-time alerts pause until it resumes.

## Incoming Telegram bot alerts

Saved Messages are outgoing self-messages and may not produce Telegram push notifications. For normal incoming Telegram notifications, create a private alert bot with Telegram's official `@BotFather`, then run:

```bash
.venv/bin/telegram-channel-tracker setup-bot
```

The command validates the BotFather token, asks you to press Start in the new bot conversation, detects your private chat, stores the token in macOS Keychain, and sends a test alert. Bot alerts are enabled only when the test succeeds. The token is never written to `config.json`.

### Launch from Spotlight on macOS

After completing the one-time Telegram setup, install the Spotlight launcher:

```bash
scripts/install_spotlight_launcher.sh
```

Search Spotlight for **Telegram Channel Tracker**. Opening it starts the local server when needed and opens the dashboard. Launcher and server logs are written to `.data/launcher.log` and `.data/server.log`.

After code changes, restart the running app with:

```bash
scripts/restart_telegram_channel_tracker.sh
```

The restart script loads the current project source and waits for a successful
health check before returning.

Defaults:

- backfill the latest 100 posts without notifications
- do not download media files unless explicitly enabled in Tracker settings
- ignore media-only posts that have no text caption
- prune downloaded media after 30 days while retaining post metadata
- enable Saved Messages alerts
- keep email alerts disabled until Gmail setup succeeds
- keep incoming Telegram bot alerts disabled until bot setup succeeds

Environment overrides: `TCT_DATA_DIR`, `TCT_API_ID`, `TCT_API_HASH`, `TCT_CHANNEL`, `TCT_HOST`, `TCT_PORT`, `TCT_GMAIL_ADDRESS`, `TCT_EMAIL_RECIPIENT`, `TCT_GMAIL_APP_PASSWORD`, `TCT_TELEGRAM_BOT_USERNAME`, `TCT_TELEGRAM_BOT_CHAT_ID`, and `TCT_TELEGRAM_BOT_TOKEN`.

## Tests

```bash
.venv/bin/pytest
```
