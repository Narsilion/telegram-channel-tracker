from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError

from telegram_channel_tracker.db import Database
from telegram_channel_tracker.live import LiveBroker
from telegram_channel_tracker.schemas import StatusResponse, TargetRecord
from telegram_channel_tracker.settings import Settings
from telegram_channel_tracker.telegram_refs import (
    is_trackable_channel,
    message_is_in_topic,
    parse_channel_target,
    resolve_channel,
    resolve_topic_id,
)


logger = logging.getLogger(__name__)


def should_archive_message(message: Any) -> bool:
    return not bool(message.media) or bool((message.message or "").strip())


class TelegramMonitor:
    def __init__(self, settings: Settings, db: Database, broker: LiveBroker, client: TelegramClient | None = None) -> None:
        if not settings.api_id or not settings.api_hash:
            raise ValueError("Telegram API credentials are not configured. Run setup first.")
        self.settings = settings
        self.db = db
        self.broker = broker
        self.client = client or TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
        self._stop = False
        self._entities: dict[int, Any] = {}
        self._reload = asyncio.Event()
        self._handlers_registered = False
        self.state = "starting"
        self.detail: str | None = None
        self.target_statuses: dict[int, tuple[str, str | None]] = {}

    def status(self, target_id: int | None = None) -> StatusResponse:
        target = self.db.get_target(target_id) if target_id is not None else next(iter(self.db.list_targets()), None)
        if target is None:
            return StatusResponse(state="unconfigured", detail="Add a Telegram target.")
        state, detail = self.target_statuses.get(target.id, ("paused" if not target.enabled else self.state, self.detail))
        cursor = self.db.get_target_state(target.id, "last_message_id")
        return StatusResponse(
            state=state,
            detail=detail,
            channel_title=target.title,
            topic_id=target.topic_id,
            last_message_id=int(cursor) if cursor else None,
        )

    def request_reload(self) -> None:
        self._reload.set()

    async def create_target(self, raw_reference: str, backfill_limit: int) -> TargetRecord:
        reference, linked_message_id = parse_channel_target(raw_reference)
        entity = await resolve_channel(self.client, raw_reference)
        if not is_trackable_channel(entity):
            raise ValueError("The selected chat is neither a Telegram channel nor a supergroup.")
        topic_id = await resolve_topic_id(self.client, entity, linked_message_id)
        stored_ref = f"@{entity.username}" if getattr(entity, "username", None) else str(entity.id)
        target = self.db.create_target(
            channel_ref=stored_ref, channel_id=int(entity.id), topic_id=topic_id,
            title=str(getattr(entity, "title", reference)), backfill_limit=backfill_limit,
        )
        self._entities[target.id] = entity
        self._reload.set()
        return target

    async def run(self) -> None:
        delay = 2
        while not self._stop:
            try:
                self.state = "connecting"
                self.detail = None
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    self.state = "unauthorized"
                    self.detail = "Telegram session is unauthorized; run setup again."
                    return
                self._register_handlers()
                await self._sync_targets()
                await self._prune_media()
                self.state = "active"
                await self.broker.broadcast({"type": "targets_updated"})
                delay = 2
                while self.client.is_connected() and not self._stop:
                    try:
                        await asyncio.wait_for(self._reload.wait(), timeout=30)
                    except TimeoutError:
                        pass
                    self._reload.clear()
                    await self._sync_targets()
                if not self._stop:
                    raise ConnectionError("Telegram connection closed")
            except FloodWaitError as exc:
                self.state = "waiting"
                self.detail = f"Telegram requested a {exc.seconds}-second pause."
                await asyncio.sleep(exc.seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Telegram monitor error")
                self.state = "retrying"
                self.detail = str(exc)
                await self.broker.broadcast({"type": "status", "status": self.status().model_dump()})
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            finally:
                if self.client.is_connected():
                    await self.client.disconnect()

    async def stop(self) -> None:
        self._stop = True
        if self.client.is_connected():
            await self.client.disconnect()

    def _register_handlers(self) -> None:
        if self._handlers_registered:
            return
        self.client.remove_event_handler(self._on_new)
        self.client.remove_event_handler(self._on_edit)
        self.client.remove_event_handler(self._on_delete)
        self.client.add_event_handler(self._on_new, events.NewMessage())
        self.client.add_event_handler(self._on_edit, events.MessageEdited())
        self.client.add_event_handler(self._on_delete, events.MessageDeleted())
        self._handlers_registered = True

    async def _sync_targets(self) -> None:
        active_ids: set[int] = set()
        for target in self.db.list_targets():
            if not target.enabled:
                self.target_statuses[target.id] = ("paused", None)
                continue
            active_ids.add(target.id)
            try:
                entity = self._entities.get(target.id) or await resolve_channel(self.client, target.channel_ref)
                self._entities[target.id] = entity
                self.target_statuses[target.id] = ("backfilling", None)
                await self._backfill_or_catch_up(target, entity)
                self.target_statuses[target.id] = ("active", None)
            except Exception as exc:
                logger.exception("Could not synchronize target %s", target.id)
                self.target_statuses[target.id] = ("retrying", str(exc))
        self._entities = {key: value for key, value in self._entities.items() if key in active_ids}
        await self.broker.broadcast({"type": "targets_updated"})

    async def _backfill_or_catch_up(self, target: TargetRecord, entity: Any) -> None:
        cursor_raw = self.db.get_target_state(target.id, "last_message_id")
        target_key = f"{getattr(entity, 'id')}:{target.topic_id or 'all'}"
        backfill_complete = self.db.get_target_state(target.id, "backfill_target") == target_key
        topic_args = {"reply_to": target.topic_id} if target.topic_id else {}
        if cursor_raw and backfill_complete:
            messages = [message async for message in self.client.iter_messages(entity, min_id=int(cursor_raw), reverse=True, **topic_args)]
        else:
            messages = [message async for message in self.client.iter_messages(entity, limit=target.backfill_limit, **topic_args)]
            messages.reverse()
        for index, message in enumerate(messages, start=1):
            await self._ingest(target, entity, message, live=False)
            if index % 10 == 0 or index == len(messages):
                await self.broker.broadcast({"type": "backfill", "target_id": target.id, "done": index, "total": len(messages)})
        if not backfill_complete:
            self.db.set_target_state(target.id, "backfill_target", target_key)

    async def _on_new(self, event: events.NewMessage.Event) -> None:
        await self._route_message(event.message, live=True)

    async def _on_edit(self, event: events.MessageEdited.Event) -> None:
        await self._route_message(event.message, live=True)

    async def _on_delete(self, event: events.MessageDeleted.Event) -> None:
        channel_id = int(utils.resolve_id(event.chat_id)[0]) if event.chat_id else 0
        post_ids = self.db.mark_deleted(channel_id, list(event.deleted_ids))
        for post_id in post_ids:
            await self.broker.broadcast({"type": "post_deleted", "post_id": post_id})

    async def _route_message(self, message: Any, *, live: bool) -> None:
        peer_channel_id = int(getattr(getattr(message, "peer_id", None), "channel_id", 0) or 0)
        for target in self.db.list_targets():
            entity = self._entities.get(target.id)
            if target.enabled and entity is not None and int(entity.id) == peer_channel_id:
                await self._ingest(target, entity, message, live=live)

    async def _ingest(self, target: TargetRecord, entity: Any, message: Any, *, live: bool) -> None:
        if not message_is_in_topic(message, target.topic_id):
            return
        if not should_archive_message(message):
            return
        username = getattr(entity, "username", None)
        channel_id = int(getattr(entity, "id"))
        message_id = int(message.id)
        payload = {
            "channel_id": channel_id,
            "telegram_message_id": message_id,
            "topic_id": target.topic_id,
            "channel_title": getattr(entity, "title", str(channel_id)),
            "channel_username": username,
            "text": message.message or "",
            "posted_at": _iso(message.date),
            "edited_at": _iso(message.edit_date) if message.edit_date else None,
            "views": message.views,
            "forwards": message.forwards,
            "grouped_id": message.grouped_id,
            "post_url": _post_url(channel_id, username, message_id),
        }
        post_id, created = self.db.upsert_post(payload)
        if message.media:
            media_record = (
                await self._download_media(entity, message, post_id)
                if self.settings.download_media
                else self._media_descriptor(message)
            )
            self.db.replace_media(post_id, [media_record])
        matched_rules = self.db.sync_matches(target.id, post_id, payload["text"])
        should_alert = False
        if live:
            for rule in matched_rules:
                if self.db.claim_delivery(post_id, rule.id, "browser"):
                    self.db.finish_delivery(post_id, rule.id, "browser")
                    should_alert = True
        if live and matched_rules and self.settings.saved_messages_alerts:
            for rule in matched_rules:
                await self._send_saved_alert(post_id, rule.id, rule.name, payload)
        post = self.db.get_post(post_id)
        await self.broker.broadcast(
            {
                "type": "post_created" if created else "post_updated",
                "target_id": target.id,
                "post": post.model_dump(mode="json") if post else None,
                "notify": should_alert,
            }
        )
        previous = int(self.db.get_target_state(target.id, "last_message_id") or 0)
        if message_id > previous:
            self.db.set_target_state(target.id, "last_message_id", str(message_id))

    async def _download_media(self, entity: Any, message: Any, post_id: int) -> dict:
        file = getattr(message, "file", None)
        size = getattr(file, "size", None)
        name = _safe_name(getattr(file, "name", None) or f"message-{message.id}{getattr(file, 'ext', '') or ''}")
        kind = _media_kind(message)
        base = self.settings.media_dir / str(getattr(entity, "id")) / str(post_id)
        if size is not None and size > self.settings.media_max_bytes:
            return {"kind": kind, "file_name": name, "mime_type": getattr(file, "mime_type", None), "size_bytes": size, "status": "skipped", "detail": "File exceeds configured size limit."}
        base.mkdir(parents=True, exist_ok=True)
        target = base / name
        try:
            downloaded = await message.download_media(file=str(target))
            return {
                "kind": kind, "file_name": name, "mime_type": getattr(file, "mime_type", None),
                "size_bytes": size, "local_path": str(Path(downloaded).resolve()) if downloaded else None,
                "status": "downloaded" if downloaded else "failed", "detail": None if downloaded else "Telegram returned no file path.",
            }
        except Exception as exc:
            logger.exception("Media download failed for message %s", message.id)
            return {"kind": kind, "file_name": name, "mime_type": getattr(file, "mime_type", None), "size_bytes": size, "status": "failed", "detail": str(exc)}

    @staticmethod
    def _media_descriptor(message: Any) -> dict:
        file = getattr(message, "file", None)
        name = _safe_name(getattr(file, "name", None) or f"message-{message.id}{getattr(file, 'ext', '') or ''}")
        return {
            "kind": _media_kind(message),
            "file_name": name,
            "mime_type": getattr(file, "mime_type", None),
            "size_bytes": getattr(file, "size", None),
            "status": "not_downloaded",
            "detail": "Media downloads are disabled.",
        }

    async def _send_saved_alert(self, post_id: int, rule_id: int, rule_name: str, payload: dict) -> None:
        destination = "saved_messages"
        if not self.db.claim_delivery(post_id, rule_id, destination):
            return
        excerpt = re.sub(r"\s+", " ", payload["text"]).strip()[:500] or "(media-only post)"
        text = f"🔔 Telegram channel alert\nRule: {rule_name}\nChannel: {payload['channel_title']}\n\n{excerpt}"
        if payload.get("post_url"):
            text += f"\n\n{payload['post_url']}"
        try:
            await self.client.send_message("me", text, link_preview=False)
            self.db.finish_delivery(post_id, rule_id, destination)
        except Exception as exc:
            self.db.finish_delivery(post_id, rule_id, destination, str(exc))
            logger.exception("Could not send Saved Messages alert")

    async def _prune_media(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=self.settings.media_retention_days)).isoformat()
        for row in self.db.media_to_prune(cutoff):
            path = Path(row["local_path"])
            try:
                if path.is_file() and path.resolve().is_relative_to(self.settings.media_dir.resolve()):
                    path.unlink()
                self.db.mark_media_pruned(int(row["id"]))
            except OSError:
                logger.exception("Could not prune media file %s", path)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _post_url(channel_id: int, username: str | None, message_id: int) -> str:
    return f"https://t.me/{username}/{message_id}" if username else f"https://t.me/c/{channel_id}/{message_id}"


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(value).name).strip("._")
    return cleaned[:180] or "attachment"


def _media_kind(message: Any) -> str:
    for kind in ("photo", "video", "audio", "voice", "document", "sticker", "gif"):
        if getattr(message, kind, None):
            return kind
    return "media"
