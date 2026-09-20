from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from telegram_channel_tracker.matching import RuleSpec, matched_spans, matches, normalize
from telegram_channel_tracker.schemas import PostRecord, RuleRecord, RuleUpsert, TargetRecord, TargetUpdate


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self, *, saved_messages_alerts: bool = True) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_ref TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    backfill_limit INTEGER NOT NULL DEFAULT 100,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel_id, topic_id)
                );
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    telegram_message_id INTEGER NOT NULL,
                    topic_id INTEGER,
                    channel_title TEXT NOT NULL,
                    channel_username TEXT,
                    text TEXT NOT NULL DEFAULT '',
                    posted_at TEXT NOT NULL,
                    edited_at TEXT,
                    deleted_at TEXT,
                    views INTEGER,
                    forwards INTEGER,
                    grouped_id INTEGER,
                    post_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel_id, telegram_message_id)
                );
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    file_name TEXT,
                    mime_type TEXT,
                    size_bytes INTEGER,
                    local_path TEXT,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(post_id, kind, file_name)
                );
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    include_terms TEXT NOT NULL,
                    match_mode TEXT NOT NULL CHECK(match_mode IN ('any', 'all')),
                    exclude_terms TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    email_alerts INTEGER NOT NULL DEFAULT 1,
                    telegram_bot_alerts INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS matches (
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
                    matched_at TEXT NOT NULL,
                    PRIMARY KEY(post_id, rule_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    delivered_at TEXT,
                    error_text TEXT,
                    PRIMARY KEY(post_id, rule_id, destination)
                );
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at DESC);
                CREATE INDEX IF NOT EXISTS idx_posts_deleted_at ON posts(deleted_at);
                """
            )
            post_columns = {row["name"] for row in connection.execute("PRAGMA table_info(posts)").fetchall()}
            if "topic_id" not in post_columns:
                connection.execute("ALTER TABLE posts ADD COLUMN topic_id INTEGER")
            rule_columns = {row["name"] for row in connection.execute("PRAGMA table_info(rules)").fetchall()}
            if "target_id" not in rule_columns:
                connection.execute("ALTER TABLE rules ADD COLUMN target_id INTEGER")
            if "saved_messages_alerts" not in rule_columns:
                connection.execute(f"ALTER TABLE rules ADD COLUMN saved_messages_alerts INTEGER NOT NULL DEFAULT {int(saved_messages_alerts)}")
            if "email_alerts" not in rule_columns:
                connection.execute("ALTER TABLE rules ADD COLUMN email_alerts INTEGER NOT NULL DEFAULT 1")
            if "telegram_bot_alerts" not in rule_columns:
                connection.execute("ALTER TABLE rules ADD COLUMN telegram_bot_alerts INTEGER NOT NULL DEFAULT 1")

    def bootstrap_legacy_target(
        self, *, channel_ref: str | None, topic_id: int | None, backfill_limit: int
    ) -> TargetRecord | None:
        targets = self.list_targets()
        if targets:
            return targets[0]
        if not channel_ref:
            return None
        channel_id = int(channel_ref) if channel_ref.lstrip("-").isdigit() else 0
        with self.connect() as connection:
            title_row = connection.execute(
                "SELECT channel_title FROM posts ORDER BY posted_at DESC LIMIT 1"
            ).fetchone()
        target = self.create_target(
            channel_ref=channel_ref,
            channel_id=channel_id,
            topic_id=topic_id,
            title=str(title_row["channel_title"]) if title_row else channel_ref,
            backfill_limit=backfill_limit,
        )
        with self.connect() as connection:
            connection.execute("UPDATE rules SET target_id=? WHERE target_id IS NULL", (target.id,))
        legacy_cursor = self.get_state("last_message_id")
        legacy_backfill = self.get_state("backfill_target")
        if legacy_cursor:
            self.set_target_state(target.id, "last_message_id", legacy_cursor)
        if legacy_backfill:
            self.set_target_state(target.id, "backfill_target", legacy_backfill)
        return target

    def create_target(
        self, *, channel_ref: str, channel_id: int, topic_id: int | None,
        title: str, backfill_limit: int = 100,
    ) -> TargetRecord:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO targets(channel_ref,channel_id,topic_id,title,enabled,backfill_limit,created_at,updated_at)
                VALUES(?,?,?,?,1,?,?,?)""",
                (channel_ref, channel_id, topic_id or 0, title, backfill_limit, now, now),
            )
            row = connection.execute("SELECT * FROM targets WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._target(row)

    def list_targets(self) -> list[TargetRecord]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM targets ORDER BY title COLLATE NOCASE, id").fetchall()
        return [self._target(row) for row in rows]

    def get_target(self, target_id: int) -> TargetRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
        return self._target(row) if row else None

    def update_target(self, target_id: int, payload: TargetUpdate) -> TargetRecord | None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE targets SET enabled=?,backfill_limit=?,updated_at=? WHERE id=?",
                (int(payload.enabled), payload.backfill_limit, utc_now(), target_id),
            )
            row = connection.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
        return self._target(row) if row else None

    def delete_target(self, target_id: int) -> bool:
        with self.connect() as connection:
            connection.execute("DELETE FROM rules WHERE target_id=?", (target_id,))
            cursor = connection.execute("DELETE FROM targets WHERE id=?", (target_id,))
            connection.execute("DELETE FROM state WHERE key LIKE ?", (f"target:{target_id}:%",))
        return cursor.rowcount > 0

    def target_stats(self, target: TargetRecord) -> dict[str, object]:
        topic_clause = "topic_id=?" if target.topic_id is not None else "1=1"
        params: tuple[object, ...] = (target.channel_id, target.topic_id) if target.topic_id is not None else (target.channel_id,)
        with self.connect() as connection:
            row = connection.execute(
                f"""SELECT COUNT(*) post_count, MAX(posted_at) last_posted_at,
                SUM(CASE WHEN EXISTS(SELECT 1 FROM matches x JOIN rules r ON r.id=x.rule_id
                    WHERE x.post_id=posts.id AND r.target_id=?) THEN 1 ELSE 0 END) matched_count
                FROM posts WHERE channel_id=? AND {topic_clause}""",
                (target.id, *params),
            ).fetchone()
            rule_row = connection.execute(
                "SELECT COUNT(*) count FROM rules WHERE target_id=? AND enabled=1", (target.id,)
            ).fetchone()
        return {
            "post_count": int(row["post_count"] or 0),
            "matched_count": int(row["matched_count"] or 0),
            "last_posted_at": row["last_posted_at"],
            "enabled_rule_count": int(rule_row["count"] or 0),
        }

    def get_target_state(self, target_id: int, key: str) -> str | None:
        return self.get_state(f"target:{target_id}:{key}")

    def set_target_state(self, target_id: int, key: str, value: str) -> None:
        self.set_state(f"target:{target_id}:{key}", value)

    def delete_target_state(self, target_id: int, key: str) -> None:
        self.delete_state(f"target:{target_id}:{key}")

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def reset_archive(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")
            connection.execute("DELETE FROM state WHERE key='last_message_id'")

    def get_state(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def delete_state(self, key: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM state WHERE key=?", (key,))

    def delete_media_only_posts(self) -> tuple[int, list[str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT m.local_path FROM media m JOIN posts p ON p.id=m.post_id
                WHERE TRIM(p.text)='' AND m.local_path IS NOT NULL"""
            ).fetchall()
            cursor = connection.execute(
                """DELETE FROM posts WHERE TRIM(text)='' AND EXISTS(
                SELECT 1 FROM media m WHERE m.post_id=posts.id)"""
            )
        return cursor.rowcount, [str(row["local_path"]) for row in rows]

    def upsert_post(self, payload: dict) -> tuple[int, bool]:
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM posts WHERE channel_id=? AND telegram_message_id=?",
                (payload["channel_id"], payload["telegram_message_id"]),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO posts(
                    channel_id, telegram_message_id, topic_id, channel_title, channel_username, text,
                    posted_at, edited_at, views, forwards, grouped_id, post_url, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, telegram_message_id) DO UPDATE SET
                    topic_id=excluded.topic_id, channel_title=excluded.channel_title, channel_username=excluded.channel_username,
                    text=excluded.text, edited_at=excluded.edited_at, views=excluded.views,
                    forwards=excluded.forwards, grouped_id=excluded.grouped_id,
                    post_url=excluded.post_url, updated_at=excluded.updated_at
                """,
                (
                    payload["channel_id"], payload["telegram_message_id"], payload.get("topic_id"), payload["channel_title"],
                    payload.get("channel_username"), payload.get("text", ""), payload["posted_at"],
                    payload.get("edited_at"), payload.get("views"), payload.get("forwards"),
                    payload.get("grouped_id"), payload.get("post_url"), now, now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM posts WHERE channel_id=? AND telegram_message_id=?",
                (payload["channel_id"], payload["telegram_message_id"]),
            ).fetchone()
        return int(row["id"]), existing is None

    def mark_deleted(self, channel_id: int, message_ids: list[int]) -> list[int]:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT id FROM posts WHERE channel_id=? AND telegram_message_id IN ({placeholders})",
                (channel_id, *message_ids),
            ).fetchall()
            connection.execute(
                f"UPDATE posts SET deleted_at=?, updated_at=? WHERE channel_id=? AND telegram_message_id IN ({placeholders})",
                (now, now, channel_id, *message_ids),
            )
        return [int(row["id"]) for row in rows]

    def replace_media(self, post_id: int, records: list[dict]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM media WHERE post_id=?", (post_id,))
            for item in records:
                connection.execute(
                    """INSERT INTO media(post_id, kind, file_name, mime_type, size_bytes, local_path, status, detail, created_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        post_id, item["kind"], item.get("file_name"), item.get("mime_type"),
                        item.get("size_bytes"), item.get("local_path"), item["status"],
                        item.get("detail"), utc_now(),
                    ),
                )

    def list_rules(self, target_id: int | None = None, *, enabled_only: bool = False) -> list[RuleRecord]:
        clauses = []
        params: list[object] = []
        if target_id is not None:
            clauses.append("target_id=?")
            params.append(target_id)
        if enabled_only:
            clauses.append("enabled=1")
        sql = "SELECT * FROM rules" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY name COLLATE NOCASE"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._rule(row) for row in rows]

    def create_rule(self, target_id: int | RuleUpsert, payload: RuleUpsert | None = None) -> RuleRecord:
        if payload is None:
            payload = target_id  # type: ignore[assignment]
            target_id = 0
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO rules(
                    name, include_terms, match_mode, exclude_terms, enabled,
                    saved_messages_alerts, email_alerts, telegram_bot_alerts, created_at, updated_at, target_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload.name, json.dumps(payload.include_terms), payload.match_mode,
                    json.dumps(payload.exclude_terms), int(payload.enabled),
                    int(payload.saved_messages_alerts), int(payload.email_alerts), int(payload.telegram_bot_alerts), now, now, target_id,
                ),
            )
            row = connection.execute("SELECT * FROM rules WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._rule(row)

    def update_rule(self, rule_id: int, payload: RuleUpsert) -> RuleRecord | None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE rules SET name=?, include_terms=?, match_mode=?, exclude_terms=?,
                enabled=?, saved_messages_alerts=?, email_alerts=?, telegram_bot_alerts=?, updated_at=? WHERE id=?""",
                (
                    payload.name, json.dumps(payload.include_terms), payload.match_mode,
                    json.dumps(payload.exclude_terms), int(payload.enabled),
                    int(payload.saved_messages_alerts), int(payload.email_alerts), int(payload.telegram_bot_alerts), utc_now(), rule_id,
                ),
            )
            row = connection.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        return self._rule(row) if row else None

    def delete_rule(self, rule_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        return cursor.rowcount > 0

    def sync_matches(self, target_id: int, post_id: int | str, text: str | None = None) -> list[RuleRecord]:
        if text is None:
            text = str(post_id)
            post_id = target_id
            target_id = 0
        rules = self.list_rules(target_id, enabled_only=True)
        matched = [rule for rule in rules if matches(text, RuleSpec(tuple(rule.include_terms), rule.match_mode, tuple(rule.exclude_terms)))]
        now = utc_now()
        with self.connect() as connection:
            connection.execute("DELETE FROM matches WHERE post_id=?", (post_id,))
            connection.executemany(
                "INSERT INTO matches(post_id, rule_id, matched_at) VALUES(?,?,?)",
                [(post_id, rule.id, now) for rule in matched],
            )
        return matched

    def recompute_all_matches(self, target_id: int | None = None) -> None:
        target = self.get_target(target_id) if target_id is not None else None
        with self.connect() as connection:
            if target is None:
                posts = connection.execute("SELECT id, text FROM posts").fetchall()
            elif target.topic_id is None:
                posts = connection.execute("SELECT id,text FROM posts WHERE channel_id=?", (target.channel_id,)).fetchall()
            else:
                posts = connection.execute("SELECT id,text FROM posts WHERE channel_id=? AND topic_id=?", (target.channel_id, target.topic_id)).fetchall()
        for post in posts:
            self.sync_matches(target_id or 0, int(post["id"]), str(post["text"]))

    def claim_delivery(self, post_id: int, rule_id: int, destination: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM deliveries WHERE post_id=? AND rule_id=? AND destination=?",
                (post_id, rule_id, destination),
            ).fetchone()
            if row and row["status"] in {"pending", "delivered"}:
                return False
            connection.execute(
                """INSERT INTO deliveries(post_id, rule_id, destination, status, attempted_at)
                VALUES(?,?,?,'pending',?) ON CONFLICT(post_id,rule_id,destination) DO UPDATE SET
                status='pending', attempted_at=excluded.attempted_at, error_text=NULL""",
                (post_id, rule_id, destination, utc_now()),
            )
        return True

    def finish_delivery(self, post_id: int, rule_id: int, destination: str, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE deliveries SET status=?, delivered_at=?, error_text=?
                WHERE post_id=? AND rule_id=? AND destination=?""",
                ("failed" if error else "delivered", None if error else utc_now(), error, post_id, rule_id, destination),
            )

    def list_posts(self, *, query: str = "", exclude_terms: list[str] | None = None, matched: bool | None = None, days: int | None = None, topic_id: int | None = None, channel_id: int | None = None, target_id: int | None = None, limit: int = 50, offset: int = 0) -> list[PostRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if query:
            clauses.append("p.text LIKE ?")
            params.append(f"%{query}%")
        for term in dict.fromkeys(normalize(term.strip()) for term in (exclude_terms or []) if term.strip()):
            clauses.append("instr(normalize_text(p.text), ?) = 0")
            params.append(term)
        if topic_id is not None:
            clauses.append("p.topic_id=?")
            params.append(topic_id)
        if channel_id is not None:
            clauses.append("p.channel_id=?")
            params.append(channel_id)
        if days is not None:
            clauses.append("julianday(p.posted_at) >= julianday(?)")
            params.append((datetime.now(UTC) - timedelta(days=days)).isoformat())
        if matched is True:
            clauses.append("EXISTS(SELECT 1 FROM matches x JOIN rules r ON r.id=x.rule_id WHERE x.post_id=p.id" + (" AND r.target_id=?" if target_id is not None else "") + ")")
            if target_id is not None:
                params.append(target_id)
        elif matched is False:
            clauses.append("NOT EXISTS(SELECT 1 FROM matches x JOIN rules r ON r.id=x.rule_id WHERE x.post_id=p.id" + (" AND r.target_id=?" if target_id is not None else "") + ")")
            if target_id is not None:
                params.append(target_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            connection.create_function("normalize_text", 1, normalize, deterministic=True)
            rows = connection.execute(
                f"SELECT p.* FROM posts p{where} ORDER BY p.posted_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [self._post(connection, row) for row in rows]

    def get_post(self, post_id: int) -> PostRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            return self._post(connection, row) if row else None

    def media_to_prune(self, cutoff: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """SELECT m.id, m.local_path FROM media m JOIN posts p ON p.id=m.post_id
                WHERE m.status='downloaded' AND m.local_path IS NOT NULL AND p.posted_at < ?""",
                (cutoff,),
            ).fetchall()

    def mark_media_pruned(self, media_id: int) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE media SET status='pruned', local_path=NULL WHERE id=?", (media_id,))

    @staticmethod
    def _rule(row: sqlite3.Row) -> RuleRecord:
        return RuleRecord(
            id=row["id"], target_id=int(row["target_id"] or 0), name=row["name"], include_terms=json.loads(row["include_terms"]),
            match_mode=row["match_mode"], exclude_terms=json.loads(row["exclude_terms"]),
            enabled=bool(row["enabled"]), saved_messages_alerts=bool(row["saved_messages_alerts"]), email_alerts=bool(row["email_alerts"]),
            telegram_bot_alerts=bool(row["telegram_bot_alerts"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _target(row: sqlite3.Row) -> TargetRecord:
        return TargetRecord(
            id=int(row["id"]), channel_ref=str(row["channel_ref"]), channel_id=int(row["channel_id"]),
            topic_id=int(row["topic_id"]) or None, title=str(row["title"]), enabled=bool(row["enabled"]),
            backfill_limit=int(row["backfill_limit"]), created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _post(connection: sqlite3.Connection, row: sqlite3.Row) -> PostRecord:
        rule_rows = connection.execute(
            "SELECT r.name, r.include_terms FROM matches x JOIN rules r ON r.id=x.rule_id WHERE x.post_id=? ORDER BY r.name", (row["id"],)
        ).fetchall()
        media_rows = connection.execute(
            "SELECT kind,file_name,mime_type,size_bytes,local_path,status,detail FROM media WHERE post_id=? ORDER BY id", (row["id"],)
        ).fetchall()
        return PostRecord(
            id=row["id"], telegram_message_id=row["telegram_message_id"], channel_id=row["channel_id"],
            channel_title=row["channel_title"], channel_username=row["channel_username"], text=row["text"],
            posted_at=row["posted_at"], edited_at=row["edited_at"], deleted_at=row["deleted_at"], views=row["views"],
            forwards=row["forwards"], grouped_id=row["grouped_id"], post_url=row["post_url"],
            matched_spans=matched_spans(row["text"], [term for r in rule_rows for term in json.loads(r["include_terms"])]),
            matched_rule_names=[r["name"] for r in rule_rows], media=[dict(m) for m in media_rows],
        )
