import asyncio

from telegram_channel_tracker.telegram_refs import (
    is_trackable_channel,
    message_is_in_topic,
    parse_channel_reference,
    parse_channel_target,
    resolve_channel,
    resolve_topic_id,
)


class Entity:
    def __init__(self, entity_id: int) -> None:
        self.id = entity_id


class Dialog:
    def __init__(self, entity: Entity) -> None:
        self.entity = entity


class FakeClient:
    def __init__(self) -> None:
        self.entities = [Entity(111), Entity(1163031069)]
        self.direct_reference = None

    async def iter_dialogs(self):
        for entity in self.entities:
            yield Dialog(entity)

    async def get_entity(self, reference):
        self.direct_reference = reference
        return Entity(999)


def test_parses_private_message_link_as_channel_id() -> None:
    assert parse_channel_reference("https://t.me/c/1163031069/21043") == 1163031069
    assert parse_channel_target("https://t.me/c/1163031069/21043") == (1163031069, 21043)


def test_parses_public_message_link_as_username() -> None:
    assert parse_channel_reference("https://t.me/example_channel/42") == "@example_channel"


def test_parses_marked_channel_id() -> None:
    assert parse_channel_reference("-1001163031069") == 1163031069


def test_resolves_private_link_from_joined_dialogs() -> None:
    client = FakeClient()
    result = asyncio.run(resolve_channel(client, "https://t.me/c/1163031069/21043"))
    assert result.id == 1163031069
    assert client.direct_reference is None


def test_broadcast_channels_and_supergroups_are_trackable() -> None:
    broadcast = type("Broadcast", (), {"broadcast": True, "megagroup": False})()
    supergroup = type("Supergroup", (), {"broadcast": False, "megagroup": True})()
    private_chat = type("PrivateChat", (), {"broadcast": False, "megagroup": False})()
    assert is_trackable_channel(broadcast)
    assert is_trackable_channel(supergroup)
    assert not is_trackable_channel(private_chat)


def test_message_topic_filter() -> None:
    direct = type("Message", (), {"id": 21043, "reply_to": None, "forum_topic": True})()
    reply = type("Reply", (), {"reply_to_top_id": 21043, "reply_to_msg_id": 21050})()
    inside = type("Message", (), {"id": 22000, "reply_to": reply, "forum_topic": True})()
    other_reply = type("Reply", (), {"reply_to_top_id": 999, "reply_to_msg_id": 1000})()
    outside = type("Message", (), {"id": 22001, "reply_to": other_reply, "forum_topic": True})()
    assert message_is_in_topic(direct, 21043)
    assert message_is_in_topic(inside, 21043)
    assert not message_is_in_topic(outside, 21043)
    assert message_is_in_topic(outside, None)


def test_direct_topic_reply_matches_when_telegram_omits_forum_flag() -> None:
    reply = type("Reply", (), {"reply_to_top_id": None, "reply_to_msg_id": 21043})()
    message = type("Message", (), {"id": 22002, "reply_to": reply, "forum_topic": None})()
    assert message_is_in_topic(message, 21043)


def test_message_link_resolves_to_topic_root() -> None:
    reply = type("Reply", (), {"reply_to_top_id": 21043, "reply_to_msg_id": 786400})()
    message = type("Message", (), {"reply_to": reply, "action": None})()
    entity = type("Entity", (), {"megagroup": True})()
    client = type("Client", (), {"get_messages": lambda self, entity, ids: _async_value(message)})()
    assert asyncio.run(resolve_topic_id(client, entity, 786460)) == 21043


async def _async_value(value):
    return value
