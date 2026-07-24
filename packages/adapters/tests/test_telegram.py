"""Telegram bot adapter: update normalization and webhook scoping/secret."""

import json

import pytest
from caspian_adapters.base import (
    InboundCommand,
    InboundMessage,
    InboundReaction,
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
