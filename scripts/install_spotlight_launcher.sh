#!/bin/zsh

set -e

PROJECT_DIR="/Users/darkcreation/Documents/git_repos/telegram-channel-tracker"
APP_DIR="${HOME}/Applications/Telegram Channel Tracker.app"
LAUNCHER_SCRIPT="${PROJECT_DIR}/scripts/launch_telegram_channel_tracker.sh"

mkdir -p "${HOME}/Applications"
rm -rf "${APP_DIR}"

chmod +x "${LAUNCHER_SCRIPT}"
/usr/bin/osacompile -o "${APP_DIR}" -e "do shell script quoted form of \"${LAUNCHER_SCRIPT}\""

echo "Installed ${APP_DIR}"
echo "Open it from Spotlight by searching for: Telegram Channel Tracker"

