from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RuleUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    include_terms: list[str] = Field(min_length=1)
    match_mode: Literal["any", "all"] = "any"
    exclude_terms: list[str] = Field(default_factory=list)
    enabled: bool = True
    saved_messages_alerts: bool = True
    email_alerts: bool = True
    telegram_bot_alerts: bool = True

    @field_validator("include_terms", "exclude_terms")
    @classmethod
    def clean_terms(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(term.strip() for term in value if term.strip()))


class RuleRecord(RuleUpsert):
    id: int
    target_id: int
    created_at: str
    updated_at: str


class TargetCreate(BaseModel):
    channel_ref: str = Field(min_length=1)
    backfill_limit: int = Field(default=100, ge=1, le=10_000)


class TargetUpdate(BaseModel):
    enabled: bool = True
    backfill_limit: int = Field(default=100, ge=1, le=10_000)


class TargetRecord(BaseModel):
    id: int
    channel_ref: str
    channel_id: int
    topic_id: int | None = None
    title: str
    enabled: bool
    backfill_limit: int
    created_at: str
    updated_at: str


class TargetCard(TargetRecord):
    status: str = "starting"
    status_detail: str | None = None
    post_count: int = 0
    matched_count: int = 0
    last_posted_at: str | None = None
    enabled_rule_count: int = 0


class SettingsUpdate(BaseModel):
    channel_ref: str = Field(min_length=1)
    backfill_limit: int = Field(default=100, ge=1, le=10_000)
    download_media: bool = False
    media_max_mb: int = Field(default=25, ge=1, le=2_000)
    media_retention_days: int = Field(default=30, ge=1, le=3650)


class PostRecord(BaseModel):
    id: int
    telegram_message_id: int
    channel_id: int
    channel_title: str
    channel_username: str | None = None
    text: str
    posted_at: str
    edited_at: str | None = None
    deleted_at: str | None = None
    views: int | None = None
    forwards: int | None = None
    grouped_id: int | None = None
    post_url: str | None = None
    matched_spans: list[tuple[int, int]] = Field(default_factory=list)
    matched_rule_names: list[str] = Field(default_factory=list)
    media: list[dict] = Field(default_factory=list)


class StatusResponse(BaseModel):
    state: str
    detail: str | None = None
    channel_title: str | None = None
    topic_id: int | None = None
    last_message_id: int | None = None
