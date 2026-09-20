# Telegram Channel Tracker

A local web dashboard that uses your personal Telegram account to track multiple channels or supergroup topics, match target-specific keyword rules, show browser notifications, and send matching alerts to **Saved Messages**, email, or a private Telegram bot.

The tracker is read-only toward configured channels. It does not post, react, forward, or perform moderation actions in them. Its outbound Telegram actions are limited to configured alerts sent to your own Saved Messages or private alert-bot chat.

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

Open <http://127.0.0.1:8775>. The main page shows one card per tracked channel or topic. Add targets by username or Telegram link, then open a card to manage its rules and archive. Each rule independently controls email, Telegram-bot, and Saved Messages notifications. Email and Telegram-bot delivery require account setup; there are no global notification switches. A live match is sent to the selected destinations once per matching rule and shown as a browser notification when the dashboard is open and notification permission is granted.

## Message filtering

In **Posts**, use **Exclude words or phrases** to hide messages containing any comma-separated term. Combine exclusions with search, matched/unmatched, and date filters. Clearing the field restores the list; exclusions here do not change archived messages or alerts.

Exclusions ignore case and normalize Unicode, and match anywhere within text (including inside longer words). For example, `sponsored, sold out` excludes a message containing either phrase. Symbols such as `%` and `_` are treated literally.

The posts API accepts repeated query parameters: `?exclude_terms=sponsored&exclude_terms=sold%20out` on `/api/posts` and `/api/targets/{target_id}/posts`.

## Gmail alerts

Open **Settings** from the home or channel page. Under **Gmail**, enter the sender, recipient, and Google App Password, then choose **Send test email & save**. Enable Google 2-Step Verification and create the App Password in your Google account first; the page links to it.

A successful test saves the addresses in `config.json` and the password in macOS Keychain. Failed setup preserves the previous working configuration. Leave the password blank to keep the stored password for the same sender. **Test saved email setup** sends another test without changing settings. SMTP uses SSL and retries delivery failures up to three attempts.

UI changes take effect immediately. Select **Email** in each rule to receive live matches; historical imports do not send alerts. The Mac must remain awake, online, and running the tracker.

The CLI remains available: `.venv/bin/telegram-channel-tracker setup-email`. It uses the same test-before-save flow; restart an already running tracker after CLI setup.

## Incoming Telegram bot alerts

Saved Messages are outgoing self-messages and may not produce Telegram push notifications. For incoming alerts, create a dedicated bot with `@BotFather` using `/newbot`, then open **Settings → Telegram bot**:

1. Paste the token and choose **Connect bot**.
2. Follow **Open in Telegram** and press **Start** using the recipient account.
3. Choose **Send test alert & save**.

The unique connection link identifies your private chat and expires after ten minutes. Bots already using a webhook need a separate dedicated alert bot. After a successful test, the token is stored in macOS Keychain and the bot identity and recipient in `config.json`; failed setup preserves previous settings. **Test saved bot setup** sends another test using the stored token. Select **Telegram** in each rule for live alerts.

The CLI remains available: `.venv/bin/telegram-channel-tracker setup-bot`. It uses the same connection and test-before-save flow; restart an already running tracker after CLI setup.

Passwords and tokens are never returned by the Settings API or stored in browser storage. Settings saved through the UI or CLI use a dedicated Keychain reference; legacy credentials remain supported. Saved setup takes precedence over notification environment overrides so account details remain paired with their tested credentials after a restart.

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
