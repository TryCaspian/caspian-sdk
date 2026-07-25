"""Zulip adapter: payload normalization, webhook token verification, routing."""

import json

import pytest
from comm_gateway.providers.base import Capability, WebhookVerificationError
from comm_gateway.providers.zulip import ZulipProvider, parse_outgoing_webhook

BOT_EMAIL = "bot@zulip.example.com"
WEBHOOK_TOKEN = "test-webhook-token"


def _stream_message(
    text="hello from zulip",
    stream_id=101,
    topic="general",
    message_id=5001,
    sender_email="alice@zulip.example.com",
    sender_name="Alice Smith",
    token=WEBHOOK_TOKEN,
):
    return {
        "message": {
            "id": message_id,
            "content": text,
            "sender_email": sender_email,
            "sender_full_name": sender_name,
            "type": "stream",
            "stream_id": stream_id,
            "subject": topic,
            "display_recipient": "general",
        },
        "bot_email": BOT_EMAIL,
        "token": token,
    }


def _dm_message(
    text="private hello",
    message_id=5002,
    sender_email="bob@zulip.example.com",
    sender_name="Bob Jones",
    sender_id=42,
    token=WEBHOOK_TOKEN,
):
    return {
        "message": {
            "id": message_id,
            "content": text,
            "sender_email": sender_email,
            "sender_full_name": sender_name,
            "sender_id": sender_id,
            "type": "private",
            "subject": "",
            "display_recipient": [
                {"id": sender_id, "email": sender_email},
                {"id": 99, "email": BOT_EMAIL},
            ],
        },
        "bot_email": BOT_EMAIL,
        "token": token,
    }


def test_parse_stream_message_normalizes():
    inbound = parse_outgoing_webhook(_stream_message(), BOT_EMAIL)
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "hello from zulip"
    assert msg.sender_address == "alice@zulip.example.com"
    assert msg.sender_name == "Alice Smith"
    assert msg.provider_inbox_id == BOT_EMAIL
    assert msg.provider_message_id == "101:5001"
    assert msg.provider_thread_id == "101:general"
    assert msg.chat_type == "channel"


def test_parse_dm_message_normalizes():
    inbound = parse_outgoing_webhook(_dm_message(), BOT_EMAIL)
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "private hello"
    assert msg.sender_address == "bob@zulip.example.com"
    assert msg.chat_type == "private"
    # DM thread key is canonical: sorted participant ids
    assert msg.provider_thread_id == "dm:42,99"


def test_parse_skips_empty_payload():
    assert parse_outgoing_webhook({}, BOT_EMAIL) == []
    assert parse_outgoing_webhook({"message": {}}, BOT_EMAIL) == []
    assert parse_outgoing_webhook({"message": {"content": "hi"}}, BOT_EMAIL) == []


def test_parse_skips_non_dict_message():
    assert parse_outgoing_webhook({"message": []}, BOT_EMAIL) == []
    assert parse_outgoing_webhook({"message": "text"}, BOT_EMAIL) == []


def test_parse_webhook_accepts_valid_token():
    provider = ZulipProvider(webhook_token=WEBHOOK_TOKEN)
    payload = json.dumps(_stream_message()).encode()
    creds = {"bot_email": BOT_EMAIL, "bot_api_key": "fake-key"}
    inbound = provider.parse_webhook(payload, {}, credentials=creds)
    assert inbound[0].text == "hello from zulip"


def test_parse_webhook_rejects_bad_token():
    provider = ZulipProvider(webhook_token=WEBHOOK_TOKEN)
    payload = json.dumps(_stream_message(token="wrong-token")).encode()
    creds = {"bot_email": BOT_EMAIL, "bot_api_key": "fake-key"}
    with pytest.raises(WebhookVerificationError, match="bot token mismatch"):
        provider.parse_webhook(payload, {}, credentials=creds)


def test_parse_webhook_rejects_missing_token():
    provider = ZulipProvider(webhook_token=WEBHOOK_TOKEN)
    data = _stream_message()
    del data["token"]
    payload = json.dumps(data).encode()
    creds = {"bot_email": BOT_EMAIL, "bot_api_key": "fake-key"}
    with pytest.raises(WebhookVerificationError, match="bot token mismatch"):
        provider.parse_webhook(payload, {}, credentials=creds)


def test_parse_webhook_requires_scope_without_token():
    provider = ZulipProvider()
    with pytest.raises(WebhookVerificationError, match="require"):
        provider.parse_webhook(b'{"token": "x"}', {}, credentials=None)


def test_parse_webhook_rejects_invalid_json():
    provider = ZulipProvider(webhook_token=WEBHOOK_TOKEN)
    creds = {"bot_email": BOT_EMAIL, "bot_api_key": "fake-key"}
    with pytest.raises(WebhookVerificationError, match="invalid JSON"):
        provider.parse_webhook(b"not json", {}, credentials=creds)


def test_parse_webhook_without_token_skips_check():
    provider = ZulipProvider()
    payload = json.dumps(_stream_message()).encode()
    creds = {"bot_email": BOT_EMAIL, "bot_api_key": "fake-key"}
    inbound = provider.parse_webhook(payload, {}, credentials=creds)
    assert inbound[0].provider_inbox_id == BOT_EMAIL


def test_parse_webhook_falls_back_to_payload_bot_email():
    provider = ZulipProvider()
    payload = json.dumps(_stream_message()).encode()
    creds = {"bot_email": "", "bot_api_key": "fake-key"}
    inbound = provider.parse_webhook(payload, {}, credentials=creds)
    assert inbound[0].provider_inbox_id == BOT_EMAIL


def test_capabilities_are_honest():
    caps = ZulipProvider.capabilities
    assert Capability.RECEIVE in caps
    assert Capability.REPLY in caps
    assert Capability.SEND in caps
    assert Capability.INITIATE not in caps
    assert Capability.BACKFILL not in caps
