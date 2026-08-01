from telegram_channel_tracker.telegram_service import should_archive_message


def message(*, text: str, media: bool):
    return type("Message", (), {"message": text, "media": object() if media else None})()


def test_media_only_message_is_not_archived() -> None:
    assert not should_archive_message(message(text="", media=True))
    assert not should_archive_message(message(text="   ", media=True))


def test_captioned_media_and_text_messages_are_archived() -> None:
    assert should_archive_message(message(text="Caption", media=True))
    assert should_archive_message(message(text="Text", media=False))

