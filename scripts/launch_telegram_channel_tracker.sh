#!/bin/zsh

set -e

PROJECT_DIR="/Users/darkcreation/Documents/git_repos/telegram-channel-tracker"
PYTHON_BIN="/usr/local/bin/python3"
APP_URL="http://127.0.0.1:8775"
HEALTH_URL="${APP_URL}/api/health"
DATA_DIR="${PROJECT_DIR}/.data"
LAUNCHER_LOG="${DATA_DIR}/launcher.log"
SERVER_LOG="${DATA_DIR}/server.log"

mkdir -p "${DATA_DIR}"
exec >> "${LAUNCHER_LOG}" 2>&1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"Telegram Channel Tracker\"" >/dev/null 2>&1 || true
}

health_ok() {
  /usr/bin/curl -fsS --max-time 2 "${HEALTH_URL}" >/dev/null
}

wait_for_health() {
  local attempt
  for attempt in {1..30}; do
    if health_ok; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

log "Launcher invoked"

if [[ -f "${HOME}/.zshrc" ]]; then
  log "Sourcing ${HOME}/.zshrc"
  set +e
  source "${HOME}/.zshrc"
  set -e
fi

cd "${PROJECT_DIR}"

if health_ok; then
  log "Server already running"
  log "Opening ${APP_URL}"
  /usr/bin/open "${APP_URL}"
  exit 0
fi

if [[ ! -x ".venv/bin/telegram-channel-tracker" ]]; then
  log "Creating/updating project virtual environment"
  notify "Preparing the local app environment. This can take a minute on first launch."
  "${PYTHON_BIN}" -m venv .venv
  .venv/bin/python -m pip install .
fi

log "Starting Telegram Channel Tracker server"
notify "Starting local server..."
nohup env TCT_PROJECT_DIR="${PROJECT_DIR}" PYTHONPATH="${PROJECT_DIR}" .venv/bin/python -m telegram_channel_tracker.main run > "${SERVER_LOG}" 2>&1 &
if ! wait_for_health; then
  log "Server did not become healthy; see ${SERVER_LOG}"
  notify "Could not start. Check .data/server.log."
  exit 1
fi

log "Opening ${APP_URL}"
/usr/bin/open "${APP_URL}"
