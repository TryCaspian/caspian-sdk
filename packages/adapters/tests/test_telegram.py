"""Telegram bot adapter: update normalization and webhook scoping/secret."""

import json

import pytest
from caspian_adapters.base import WebhookVerificationError
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


def test_parse_webhook_without_secret_skips_check():
    provider = TelegramProvider()
    payload = json.dumps(_update()).encode()
    inbound = provider.parse_webhook(payload, {}, credentials={"bot_token": BOT_TOKEN})
    assert inbound[0].provider_inbox_id == BOT_ID
