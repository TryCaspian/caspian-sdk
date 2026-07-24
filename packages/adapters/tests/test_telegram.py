"""Telegram bot adapter: update normalization and webhook scoping/secret."""

import json

import httpx
import pytest
from caspian_adapters.base import (
    Attachment,
    InboundCommand,
    InboundMessage,
    InboundReaction,
    OutboundMessage,
    WebhookVerificationError,
)
from caspian_adapters.telegram import TelegramProvider, bot_id_from_token, parse_update

BOT_TOKEN = "7123456789:AAEexample"
BOT_ID = "7123456789"


def _update(text="hi there", update_id=100, edited=False, chat_type="private"):
    message = {
        "message_id": 55,
        "chat": {"id": 900, "type": chat_type},
        "from": {"id": 42, "username": "alice", "first_name": "Alice", "last_name": "Ng"},
        "text": text,
    }
    return {"update_id": update_id, ("edited_message" if edited else "message"): message}


def test_bot_id_from_token():
    assert bot_id_from_token(BOT_TOKEN) == BOT_ID


def test_parse_update_normalizes_message():
    inbound = parse_update(_update(), BOT_ID)
    assert len(inbound) == 1
    assert inbound[0].text == "hi there"
    assert inbound[0].provider_inbox_id == BOT_ID
    assert inbound[0].provider_message_id == "900:55"
    assert inbound[0].sender_address == "alice"
    assert inbound[0].sender_name == "Alice Ng"
    assert inbound[0].edited is False


def test_parse_update_marks_edited_and_group_chats():
    inbound = parse_update(_update(edited=True, chat_type="group"), BOT_ID)
    assert inbound[0].edited is True
    assert inbound[0].chat_type == "group"


def test_parse_update_skips_textless():
    assert parse_update({"update_id": 1, "message": {"chat": {"id": 1}}}, BOT_ID) == []
    assert parse_update({"update_id": 2}, BOT_ID) == []


def test_parse_webhook_requires_connection_scope():
    provider = TelegramProvider()
    with pytest.raises(WebhookVerificationError, match="connection scope"):
        provider.parse_webhook(b"{}", {}, credentials=None)


def test_parse_webhook_enforces_secret_header():
    provider = TelegramProvider()
    payload = json.dumps(_update()).encode()
    creds = {"bot_token": BOT_TOKEN, "webhook_secret": "shh"}
    inbound = provider.parse_webhook(
        payload, {"X-Telegram-Bot-Api-Secret-Token": "shh"}, credentials=creds
    )
    assert inbound[0].text == "hi there"
    with pytest.raises(WebhookVerificationError, match="secret token"):
        provider.parse_webhook(
            payload, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}, credentials=creds
        )
    # A missing header must reject cleanly, not raise on the constant-time compare.
    with pytest.raises(WebhookVerificationError, match="secret token"):
        provider.parse_webhook(payload, {}, credentials=creds)


def test_parse_webhook_without_secret_skips_check():
    provider = TelegramProvider()
    payload = json.dumps(_update()).encode()
    inbound = provider.parse_webhook(payload, {}, credentials={"bot_token": BOT_TOKEN})
    assert inbound[0].provider_inbox_id == BOT_ID


def _media_update(update_id=200, **media):
    message = {"message_id": 60, "chat": {"id": 900, "type": "private"}, **media}
    return {"update_id": update_id, "message": message}


def test_parse_update_extracts_photo_with_caption():
    update = _media_update(
        caption="my cat",
        photo=[
            {"file_id": "small", "file_size": 111},
            {"file_id": "big", "file_size": 999},  # largest size is last
        ],
    )
    [msg] = parse_update(update, BOT_ID)
    assert msg.text == "my cat"  # caption is surfaced as text
    assert len(msg.attachments) == 1
    att = msg.attachments[0]
    assert att.provider_file_id == "big"
    assert att.mime_type == "image/jpeg"
    assert att.size_bytes == 999
    assert att.url is None  # Telegram needs a getFile call to resolve a URL


def test_parse_update_extracts_document_and_voice():
    [doc] = parse_update(
        _media_update(
            document={
                "file_id": "doc1", "file_name": "report.pdf",
                "mime_type": "application/pdf", "file_size": 2048,
            }
        ),
        BOT_ID,
    )
    assert doc.text is None
    assert doc.attachments[0].filename == "report.pdf"
    assert doc.attachments[0].mime_type == "application/pdf"
    assert doc.attachments[0].provider_file_id == "doc1"

    [voice] = parse_update(
        _media_update(voice={"file_id": "v1", "mime_type": "audio/ogg", "file_size": 512}),
        BOT_ID,
    )
    assert voice.attachments[0].mime_type == "audio/ogg"
    assert voice.attachments[0].provider_file_id == "v1"


def test_parse_update_keeps_media_only_message():
    # A photo with no caption has no text but must not be dropped.
    [inbound] = parse_update(_media_update(photo=[{"file_id": "p", "file_size": 10}]), BOT_ID)
    assert inbound.text is None
    assert inbound.attachments[0].provider_file_id == "p"


def test_inbound_attachment_serializes_to_payload():
    update = _media_update(
        document={"file_id": "d", "file_name": "a.txt", "mime_type": "text/plain",
                  "file_size": 4}
    )
    payload = parse_update(update, BOT_ID)[0].to_payload()
    assert payload["attachments"] == [
        {"url": None, "mime_type": "text/plain", "filename": "a.txt",
         "size_bytes": 4, "provider_file_id": "d"}
    ]


def _mock_provider(handler):
    provider = TelegramProvider()
    provider._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )
    return provider


def test_send_photo_uses_sendphoto_with_caption():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    provider = _mock_provider(handler)
    message = OutboundMessage(
        text="here you go",
        to=("900",),
        attachments=[Attachment(url="https://cdn/img.png", mime_type="image/png")],
    )
    result = provider.send("inbox", message, credentials={"bot_token": BOT_TOKEN})
    assert seen["path"] == f"/bot{BOT_TOKEN}/sendPhoto"
    assert seen["body"]["photo"] == "https://cdn/img.png"
    assert seen["body"]["caption"] == "here you go"
    assert result.provider_message_id == "900:77"


def test_reply_document_uses_senddocument_and_reply_ref():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 78}})

    provider = _mock_provider(handler)
    message = OutboundMessage(
        to=("900",),
        attachments=[Attachment(provider_file_id="file123", mime_type="application/pdf")],
    )
    result = provider.reply("inbox", "900:55", message, credentials={"bot_token": BOT_TOKEN})
    assert seen["path"] == f"/bot{BOT_TOKEN}/sendDocument"
    assert seen["body"]["document"] == "file123"
    assert seen["body"]["reply_to_message_id"] == 55
    assert result.provider_message_id == "900:78"


# --- Reactions ---


def _reaction_update(emoji="👍", added=True, update_id=200, message_id=55,
                     user_id=42, username="alice"):
    old = [{"type": "emoji", "emoji": "👍"}] if not added else []
    new = [{"type": "emoji", "emoji": emoji}] if added else []
    return {
        "update_id": update_id,
        "message_reaction": {
            "message_id": message_id,
            "chat": {"id": 900, "type": "private"},
            "user": {"id": user_id, "username": username, "first_name": "Alice"},
            "old_reaction": old,
            "new_reaction": new,
        },
    }


def test_parse_reaction_added():
    inbound = parse_update(_reaction_update(emoji="👍", added=True), BOT_ID)
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].emoji == "👍"
    assert inbound[0].action == "added"
    assert inbound[0].provider_thread_id == "900"
    assert inbound[0].provider_message_id == "900:55"
    assert inbound[0].chat_type == "private"
    assert inbound[0].sender_address == "alice"
    assert inbound[0].provider_inbox_id == BOT_ID


def test_parse_reaction_removed():
    inbound = parse_update(_reaction_update(emoji="👍", added=False), BOT_ID)
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].action == "removed"


def test_parse_reaction_multiple_emojis():
    update = {
        "update_id": 300,
        "message_reaction": {
            "message_id": 55,
            "chat": {"id": 900, "type": "private"},
            "user": {"id": 42, "username": "alice"},
            "old_reaction": [{"type": "emoji", "emoji": "👍"}],
            "new_reaction": [{"type": "emoji", "emoji": "❤️"}],
        },
    }
    inbound = parse_update(update, BOT_ID)
    # Should detect both the added (❤️) and removed (👍) emojis
    added = [e for e in inbound if isinstance(e, InboundReaction) and e.action == "added"]
    removed = [e for e in inbound if isinstance(e, InboundReaction) and e.action == "removed"]
    assert len(added) == 1
    assert added[0].emoji == "❤️"
    assert len(removed) == 1
    assert removed[0].emoji == "👍"


def test_parse_reaction_custom_emoji_keeps_identity():
    update = {
        "update_id": 301,
        "message_reaction": {
            "message_id": 55,
            "chat": {"id": 900, "type": "private"},
            "user": {"id": 42, "username": "alice"},
            "old_reaction": [{"type": "custom_emoji", "custom_emoji_id": "old123"}],
            "new_reaction": [{"type": "custom_emoji", "custom_emoji_id": "new456"}],
        },
    }
    inbound = parse_update(update, BOT_ID)
    added = [e for e in inbound if isinstance(e, InboundReaction) and e.action == "added"]
    removed = [e for e in inbound if isinstance(e, InboundReaction) and e.action == "removed"]
    assert added[0].emoji == "custom_emoji:new456"
    assert removed[0].emoji == "custom_emoji:old123"


# --- Bot commands ---


def _command_update(command="/start", args="", update_id=400, message_id=60):
    text = f"{command} {args}".strip() if args else command
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": 900, "type": "private"},
            "from": {"id": 42, "username": "alice", "first_name": "Alice"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(command)}],
        },
    }


def test_parse_bot_command():
    inbound = parse_update(_command_update("/start"), BOT_ID)
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundCommand)
    assert inbound[0].command == "/start"
    assert inbound[0].args is None
    assert inbound[0].text == "/start"
    assert inbound[0].sender_address == "alice"
    assert inbound[0].provider_inbox_id == BOT_ID


def test_parse_bot_command_with_args():
    inbound = parse_update(_command_update("/deploy", "staging"), BOT_ID)
    assert inbound[0].command == "/deploy"
    assert inbound[0].args == "staging"
    assert inbound[0].text == "/deploy staging"


def test_parse_non_command_message_ignored_as_command():
    """A regular text message without a bot_command entity at offset 0 is a message."""
    inbound = parse_update(_update(text="hello world"), BOT_ID)
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundMessage)
    assert not isinstance(inbound[0], InboundCommand)
