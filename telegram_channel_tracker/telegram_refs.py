from __future__ import annotations

import re
from typing import Any

from telethon.tl.types import PeerChannel


PRIVATE_LINK = re.compile(r"^(?:https?://)?(?:www\.)?t\.me/c/(\d+)(?:/\d+)?/?(?:\?.*)?$", re.IGNORECASE)
PUBLIC_LINK = re.compile(r"^(?:https?://)?(?:www\.)?t\.me/(?:s/)?([A-Za-z0-9_]{5,})(?:/\d+)?/?(?:\?.*)?$", re.IGNORECASE)


def parse_channel_target(value: str) -> tuple[str | int, int | None]:
    raw = value.strip()
    private = PRIVATE_LINK.match(raw)
    if private:
        message_match = re.search(r"/c/\d+/(\d+)", raw, re.IGNORECASE)
        return int(private.group(1)), int(message_match.group(1)) if message_match else None
    public = PUBLIC_LINK.match(raw)
    if public:
        message_match = re.search(r"/(\d+)/?(?:\?.*)?$", raw)
        return f"@{public.group(1)}", int(message_match.group(1)) if message_match else None
    if raw.lstrip("-").isdigit():
        digits = raw.lstrip("-")
        if digits.startswith("100") and len(digits) > 3:
            digits = digits[3:]
        return int(digits), None
    return raw, None


def parse_channel_reference(value: str) -> str | int:
    return parse_channel_target(value)[0]


async def resolve_channel(client: Any, value: str) -> Any:
    reference = parse_channel_reference(value)
    if isinstance(reference, int):
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if int(getattr(entity, "id", 0)) == reference:
                return entity
        try:
            return await client.get_entity(PeerChannel(reference))
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "That private channel was not found among this account's joined chats. "
                "Confirm the account is a member and paste a message link from the channel."
            ) from exc
    return await client.get_entity(reference)


def is_trackable_channel(entity: Any) -> bool:
    return bool(getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False))


def message_is_in_topic(message: Any, topic_id: int | None) -> bool:
    if topic_id is None:
        return True
    if int(getattr(message, "id", 0)) == topic_id:
        return True
    reply = getattr(message, "reply_to", None)
    top_id = getattr(reply, "reply_to_top_id", None)
    direct_id = getattr(reply, "reply_to_msg_id", None)
    return top_id == topic_id or direct_id == topic_id


async def resolve_topic_id(client: Any, entity: Any, linked_message_id: int | None) -> int | None:
    if linked_message_id is None or not getattr(entity, "megagroup", False):
        return None
    message = await client.get_messages(entity, ids=linked_message_id)
    if not message:
        raise ValueError("The linked Telegram message is unavailable to this account.")
    if type(getattr(message, "action", None)).__name__ == "MessageActionTopicCreate":
        return linked_message_id
    reply = getattr(message, "reply_to", None)
    return (
        getattr(reply, "reply_to_top_id", None)
        or getattr(reply, "reply_to_msg_id", None)
        or linked_message_id
    )
