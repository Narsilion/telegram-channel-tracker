from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response

from telegram_channel_tracker.assets import FAVICON_SVG
from telegram_channel_tracker.db import Database
from telegram_channel_tracker.bot_alerts import bot_is_configured
from telegram_channel_tracker.email_alerts import gmail_is_configured
from telegram_channel_tracker.live import LiveBroker
from telegram_channel_tracker.matching import RuleSpec, matches
from telegram_channel_tracker.schemas import (
    RuleRecord, RuleUpsert, SettingsUpdate, TargetCreate, TargetUpdate,
)
from telegram_channel_tracker.settings import Settings, save_settings
from telegram_channel_tracker.telegram_refs import parse_channel_target
from telegram_channel_tracker.telegram_service import TelegramMonitor
from telegram_channel_tracker.ui import render_dashboard, render_home, render_settings
from telegram_channel_tracker.notification_setup import NotificationSetup
from telegram_channel_tracker.notification_routes import notification_router


def create_app(settings: Settings, *, monitor: TelegramMonitor | None = None) -> FastAPI:
    db = Database(settings.db_path)
    db.initialize(saved_messages_alerts=settings.saved_messages_alerts)
    db.bootstrap_legacy_target(
        channel_ref=settings.channel_ref, topic_id=settings.topic_id,
        backfill_limit=settings.backfill_limit,
    )
    broker = LiveBroker()
    if monitor is None and settings.api_id and settings.api_hash:
        monitor = TelegramMonitor(settings, db, broker)
    task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal task
        if monitor is not None:
            task = asyncio.create_task(monitor.run(), name="telegram-monitor")
        yield
        if monitor is not None:
            await monitor.stop()
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="Telegram Channel Tracker", lifespan=lifespan)
    app.include_router(notification_router(NotificationSetup(settings)))
    app.state.db = db
    app.state.monitor = monitor
    app.state.broker = broker

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return render_home()

    @app.get("/settings", response_class=HTMLResponse)
    def notification_settings():
        return render_settings()

    @app.get("/channels/{target_id}", response_class=HTMLResponse)
    def target_dashboard(target_id: int) -> str:
        if db.get_target(target_id) is None:
            raise HTTPException(404, "Target not found.")
        return render_dashboard(target_id)

    @app.get("/favicon.svg")
    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(
            content=FAVICON_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    def status():
        if monitor is None:
            return {"state": "unconfigured", "detail": "Run telegram-channel-tracker setup first.", "channel_title": None, "topic_id": None, "last_message_id": None}
        return monitor.status()

    def target_payload(target_id: int):
        target = db.get_target(target_id)
        if target is None:
            raise HTTPException(404, "Target not found.")
        status_payload = monitor.status(target_id) if monitor else None
        return {
            **target.model_dump(),
            **db.target_stats(target),
            "status": status_payload.state if status_payload else "unconfigured",
            "status_detail": status_payload.detail if status_payload else None,
        }

    @app.get("/api/targets")
    def list_targets():
        return [target_payload(target.id) for target in db.list_targets()]

    @app.get("/api/preferences")
    def get_preferences():
        return {
            "email_configured": gmail_is_configured(settings),
            "email_recipient": settings.email_recipient,
            "telegram_bot_configured": bot_is_configured(settings),
            "telegram_bot_username": settings.telegram_bot_username,
        }

    @app.get("/api/targets/{target_id}")
    def get_target(target_id: int):
        return target_payload(target_id)

    @app.post("/api/targets", status_code=201)
    async def create_target(payload: TargetCreate):
        if monitor is None:
            raise HTTPException(503, "Telegram account is not connected.")
        try:
            target = await monitor.create_target(payload.channel_ref, payload.backfill_limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise HTTPException(409, "That channel or topic is already tracked.") from exc
            raise
        return target_payload(target.id)

    @app.put("/api/targets/{target_id}")
    def update_target(target_id: int, payload: TargetUpdate):
        previous = db.get_target(target_id)
        if previous is None:
            raise HTTPException(404, "Target not found.")
        target = db.update_target(target_id, payload)
        if payload.backfill_limit > previous.backfill_limit:
            # A completed backfill normally switches the monitor to fetching only
            # messages newer than its cursor. Invalidate that marker when the user
            # asks for a larger history window so the next sync fetches the older
            # messages too. Keep the cursor to avoid affecting live catch-up.
            db.delete_target_state(target_id, "backfill_target")
        if monitor:
            monitor.request_reload()
        return target_payload(target_id)

    @app.delete("/api/targets/{target_id}", status_code=204)
    def delete_target(target_id: int):
        if not db.delete_target(target_id):
            raise HTTPException(404, "Target not found.")
        if monitor:
            monitor.request_reload()
        return Response(status_code=204)

    @app.get("/api/targets/{target_id}/rules", response_model=list[RuleRecord])
    def target_rules(target_id: int):
        if db.get_target(target_id) is None:
            raise HTTPException(404, "Target not found.")
        return db.list_rules(target_id)

    @app.post("/api/targets/{target_id}/rules", response_model=RuleRecord, status_code=201)
    def create_target_rule(target_id: int, payload: RuleUpsert):
        if db.get_target(target_id) is None:
            raise HTTPException(404, "Target not found.")
        try:
            result = db.create_rule(target_id, payload)
            db.recompute_all_matches(target_id)
            return result
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise HTTPException(409, "A rule with that name already exists.") from exc
            raise

    @app.put("/api/targets/{target_id}/rules/{rule_id}", response_model=RuleRecord)
    def update_target_rule(target_id: int, rule_id: int, payload: RuleUpsert):
        result = db.update_rule(rule_id, payload)
        if result is None or result.target_id != target_id:
            raise HTTPException(404, "Rule not found.")
        db.recompute_all_matches(target_id)
        return result

    @app.delete("/api/targets/{target_id}/rules/{rule_id}", status_code=204)
    def delete_target_rule(target_id: int, rule_id: int):
        rule = next((item for item in db.list_rules(target_id) if item.id == rule_id), None)
        if rule is None or not db.delete_rule(rule_id):
            raise HTTPException(404, "Rule not found.")
        db.recompute_all_matches(target_id)
        return Response(status_code=204)

    @app.get("/api/targets/{target_id}/posts")
    def target_posts(
        target_id: int, q: str = "", matched: bool | None = None,
        exclude_terms: list[str] = Query(default=[]),
        days: int | None = Query(None, ge=1, le=3650),
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ):
        target = db.get_target(target_id)
        if target is None:
            raise HTTPException(404, "Target not found.")
        return db.list_posts(
            query=q, exclude_terms=exclude_terms, matched=matched, days=days, channel_id=target.channel_id,
            topic_id=target.topic_id, target_id=target.id, limit=limit, offset=offset,
        )

    @app.get("/api/settings")
    def get_settings():
        return {
            "channel_ref": (
                f"https://t.me/c/{settings.channel_ref}/{settings.topic_id}"
                if settings.topic_id and settings.channel_ref and settings.channel_ref.isdigit()
                else settings.channel_ref
            ),
            "backfill_limit": settings.backfill_limit,
            "download_media": settings.download_media,
            "media_max_mb": settings.media_max_bytes // (1024 * 1024),
            "media_retention_days": settings.media_retention_days,
            "email_configured": gmail_is_configured(settings),
            "email_recipient": settings.email_recipient,
            "telegram_bot_configured": bot_is_configured(settings),
            "telegram_bot_username": settings.telegram_bot_username,
        }

    @app.put("/api/settings")
    def update_settings(payload: SettingsUpdate):
        previous_target = (settings.channel_ref, settings.topic_id)
        reference, linked_message_id = parse_channel_target(payload.channel_ref)
        settings.channel_ref = str(reference)
        settings.topic_id = linked_message_id
        settings.backfill_limit = payload.backfill_limit
        settings.download_media = payload.download_media
        settings.media_max_bytes = payload.media_max_mb * 1024 * 1024
        settings.media_retention_days = payload.media_retention_days
        save_settings(settings)
        if previous_target != (settings.channel_ref, settings.topic_id):
            db.delete_state("last_message_id")
            db.delete_state("backfill_target")
        return get_settings()

    @app.get("/api/rules", response_model=list[RuleRecord])
    def list_rules():
        target = next(iter(db.list_targets()), None)
        return db.list_rules(target.id) if target else []

    @app.post("/api/rules", response_model=RuleRecord, status_code=201)
    def create_rule(payload: RuleUpsert):
        try:
            target = next(iter(db.list_targets()), None)
            if target is None:
                raise HTTPException(404, "No target configured.")
            result = db.create_rule(target.id, payload)
            db.recompute_all_matches(target.id)
            return result
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise HTTPException(409, "A rule with that name already exists.") from exc
            raise

    @app.post("/api/rule-preview")
    def preview_rule(payload: RuleUpsert, text: str = ""):
        return {"matches": matches(text, RuleSpec(tuple(payload.include_terms), payload.match_mode, tuple(payload.exclude_terms)))}

    @app.put("/api/rules/{rule_id}", response_model=RuleRecord)
    def update_rule(rule_id: int, payload: RuleUpsert):
        result = db.update_rule(rule_id, payload)
        if result is None:
            raise HTTPException(404, "Rule not found.")
        db.recompute_all_matches(result.target_id)
        return result

    @app.delete("/api/rules/{rule_id}", status_code=204)
    def delete_rule(rule_id: int):
        if not db.delete_rule(rule_id):
            raise HTTPException(404, "Rule not found.")
        db.recompute_all_matches()
        return Response(status_code=204)

    @app.get("/api/posts")
    def list_posts(
        q: str = "", matched: bool | None = None,
        exclude_terms: list[str] = Query(default=[]),
        days: int | None = Query(None, ge=1, le=3650),
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ):
        target = next(iter(db.list_targets()), None)
        if target is None:
            return []
        return db.list_posts(query=q, exclude_terms=exclude_terms, matched=matched, days=days, channel_id=target.channel_id, topic_id=target.topic_id, target_id=target.id, limit=limit, offset=offset)

    @app.get("/api/posts/{post_id}")
    def get_post(post_id: int):
        post = db.get_post(post_id)
        if post is None:
            raise HTTPException(404, "Post not found.")
        return post

    @app.get("/api/posts/{post_id}/media/{media_index}")
    def get_media(post_id: int, media_index: int):
        post = db.get_post(post_id)
        if post is None or media_index < 0 or media_index >= len(post.media):
            raise HTTPException(404, "Media not found.")
        raw = post.media[media_index].get("local_path")
        if not raw:
            raise HTTPException(404, "Media file is unavailable.")
        path = Path(raw).resolve()
        if not path.is_relative_to(settings.media_dir.resolve()) or not path.is_file():
            raise HTTPException(404, "Media file is unavailable.")
        return FileResponse(path, filename=post.media[media_index].get("file_name"))

    @app.websocket("/api/live")
    async def live(websocket: WebSocket):
        await broker.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await broker.disconnect(websocket)

    return app
