from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    data_dir: Path
    api_id: int | None = None
    api_hash: str | None = None
    channel_ref: str | None = None
    topic_id: int | None = None
    host: str = "127.0.0.1"
    port: int = 8775
    backfill_limit: int = 100
    download_media: bool = False
    media_max_bytes: int = 25 * 1024 * 1024
    media_retention_days: int = 30
    saved_messages_alerts: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "telegram"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"


def project_root() -> Path:
    configured = os.environ.get("TCT_PROJECT_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "pyproject.toml").is_file() and (cwd / "telegram_channel_tracker").is_dir():
        return cwd
    return Path(__file__).resolve().parents[1]


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("TCT_DATA_DIR", project_root() / ".data")).expanduser().resolve()
    raw: dict[str, object] = {}
    config_path = data_dir / "config.json"
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    api_id_raw = os.environ.get("TCT_API_ID", raw.get("api_id"))
    return Settings(
        data_dir=data_dir,
        api_id=int(api_id_raw) if api_id_raw else None,
        api_hash=os.environ.get("TCT_API_HASH") or _str_or_none(raw.get("api_hash")),
        channel_ref=os.environ.get("TCT_CHANNEL") or _str_or_none(raw.get("channel_ref")),
        topic_id=int(raw["topic_id"]) if raw.get("topic_id") else None,
        host=os.environ.get("TCT_HOST", str(raw.get("host", "127.0.0.1"))),
        port=int(os.environ.get("TCT_PORT", raw.get("port", 8775))),
        backfill_limit=int(raw.get("backfill_limit", 100)),
        download_media=bool(raw.get("download_media", False)),
        media_max_bytes=int(raw.get("media_max_bytes", 25 * 1024 * 1024)),
        media_retention_days=int(raw.get("media_retention_days", 30)),
        saved_messages_alerts=bool(raw.get("saved_messages_alerts", True)),
    )


def save_settings(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    payload.pop("data_dir")
    settings.config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(settings.config_path, 0o600)


def _str_or_none(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
