#!/bin/zsh

set -euo pipefail

PROJECT_DIR="/Users/darkcreation/Documents/git_repos/telegram-channel-tracker"
APP_URL="http://127.0.0.1:8775"
HEALTH_URL="${APP_URL}/api/health"
DATA_DIR="${PROJECT_DIR}/.data"
SERVER_LOG="${DATA_DIR}/server.log"
EXPECTED_MODULE="telegram_channel_tracker.main"
LAUNCHER_APP="${HOME}/Applications/Telegram Channel Tracker.app"

if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  echo "Telegram Channel Tracker is not installed. Run scripts/launch_telegram_channel_tracker.sh first." >&2
  exit 1
fi

mkdir -p "${DATA_DIR}"

PIDS="$(/usr/sbin/lsof -tiTCP:8775 -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${PIDS}" ]]; then
  while IFS= read -r pid; do
    command_line="$(/bin/ps -p "${pid}" -o command= 2>/dev/null || true)"
    if [[ "${command_line}" != *"-m ${EXPECTED_MODULE}"* ]]; then
      echo "Refusing to stop PID ${pid} on port 8775; it is not Telegram Channel Tracker:" >&2
      echo "${command_line}" >&2
      exit 1
    fi
    /bin/kill "${pid}"
  done <<< "${PIDS}"

  for attempt in {1..40}; do
    if ! /usr/sbin/lsof -tiTCP:8775 -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done

  if /usr/sbin/lsof -tiTCP:8775 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Telegram Channel Tracker did not stop cleanly." >&2
    exit 1
  fi
fi

if [[ ! -d "${LAUNCHER_APP}" ]]; then
  echo "Telegram Channel Tracker launcher is not installed. Run scripts/install_spotlight_launcher.sh first." >&2
  exit 1
fi

/usr/bin/open -n -gja "${LAUNCHER_APP}"

for attempt in {1..30}; do
  if /usr/bin/curl -fsS --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1; then
    echo "Telegram Channel Tracker restarted: ${APP_URL}"
    exit 0
  fi
  sleep 0.5
done

echo "Telegram Channel Tracker did not become healthy. Check ${SERVER_LOG}." >&2
exit 1
