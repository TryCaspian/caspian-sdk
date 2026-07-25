"""Teams adapter: activity normalization, HMAC signature verification, routing."""

import hashlib
import hmac
import json

import pytest
from comm_gateway.providers.base import Capability, WebhookVerificationError
from comm_gateway.providers.teams import (
    COMPOSITE_SEP,
    SIGNATURE_HEADER,
    TeamsProvider,
    _split_teams_id,
    parse_activity,
)

APP_ID = "test-app-id-1234"
APP_SECRET = "test-app-secret"

# Realistic Teams IDs (conversation ids contain colons)
CONV_ID = "19:abc123def@thread.tacv2"
ACTIVITY_ID = "1720000000001"


def _activity(
    text="hello from teams",
    conversation_id=CONV_ID,
    activity_id=ACTIVITY_ID,
    sender_id="29:user-alice-id",
    sender_name="Alice Smith",
    is_group=False,
    bot_name="",
):
    return {
        "type": "message",
        "id": activity_id,
        "text": text,
        "from": {"id": sender_id, "name": sender_name},
        "conversation": {
            "id": conversation_id,
            "isGroup": is_group,
        },
        "recipient": {"id": "28:bot-id", "name": bot_name},
        "serviceUrl": "https://smba.trafficmanager.net/teams",
    }


def _signed_headers(payload: bytes, secret=APP_SECRET):
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: sig}


def test_parse_activity_normalizes_message():
    inbound = parse_activity(_activity(), APP_ID)
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "hello from teams"
    assert msg.sender_address == "29:user-alice-id"
    assert msg.sender_name == "Alice Smith"
    assert msg.provider_inbox_id == APP_ID
    assert msg.provider_message_id == f"{CONV_ID}{COMPOSITE_SEP}{ACTIVITY_ID}"
    assert msg.provider_thread_id == CONV_ID
    assert msg.chat_type == "private"


def test_split_teams_id_handles_colons_in_conversation():
    """Conversation ids with colons split correctly on the pipe separator."""
    mid = f"{CONV_ID}{COMPOSITE_SEP}{ACTIVITY_ID}"
    conv, act = _split_teams_id(mid)
    assert conv == CONV_ID
    assert act == ACTIVITY_ID


def test_parse_activity_group_chat():
    inbound = parse_activity(_activity(is_group=True), APP_ID)
    assert inbound[0].chat_type == "group"


def test_parse_activity_channel_conversation():
    data = _activity()
    data["conversation"]["conversationType"] = "channel"
    inbound = parse_activity(data, APP_ID)
    assert inbound[0].chat_type == "group"


def test_parse_activity_strips_bot_mention():
    text = "<at>MyBot</at> what is the weather?"
    inbound = parse_activity(_activity(text=text, bot_name="MyBot"), APP_ID)
    assert inbound[0].text == "what is the weather?"


def test_parse_activity_skips_non_message():
    data = _activity()
    data["type"] = "typing"
    assert parse_activity(data, APP_ID) == []


def test_parse_activity_skips_textless():
    data = _activity()
    data["text"] = None
    assert parse_activity(data, APP_ID) == []
    assert parse_activity({"type": "message"}, APP_ID) == []


def test_parse_webhook_accepts_valid_signature():
    provider = TeamsProvider(app_id=APP_ID, app_secret=APP_SECRET)
    payload = json.dumps(_activity()).encode()
    creds = {"app_id": APP_ID, "app_secret": APP_SECRET}
    inbound = provider.parse_webhook(payload, _signed_headers(payload), credentials=creds)
    assert inbound[0].text == "hello from teams"


def test_parse_webhook_rejects_bad_signature():
    provider = TeamsProvider(app_id=APP_ID, app_secret=APP_SECRET)
    payload = json.dumps(_activity()).encode()
    creds = {"app_id": APP_ID, "app_secret": APP_SECRET}
    with pytest.raises(WebhookVerificationError, match="HMAC signature mismatch"):
        provider.parse_webhook(
            payload, _signed_headers(payload, secret="wrong-secret"), credentials=creds
        )


def test_parse_webhook_rejects_missing_signature():
    provider = TeamsProvider(app_id=APP_ID, app_secret=APP_SECRET)
    payload = json.dumps(_activity()).encode()
    creds = {"app_id": APP_ID, "app_secret": APP_SECRET}
    with pytest.raises(WebhookVerificationError, match="HMAC signature mismatch"):
        provider.parse_webhook(payload, {}, credentials=creds)


def test_parse_webhook_requires_scope_without_secret():
    provider = TeamsProvider()
    with pytest.raises(WebhookVerificationError, match="require"):
        provider.parse_webhook(b"{}", {}, credentials=None)


def test_parse_webhook_rejects_invalid_json():
    provider = TeamsProvider(app_id=APP_ID, app_secret=APP_SECRET)
    creds = {"app_id": APP_ID, "app_secret": APP_SECRET}
    with pytest.raises(WebhookVerificationError, match="invalid JSON"):
        provider.parse_webhook(
            b"not json", _signed_headers(b"not json"), credentials=creds
        )


def test_parse_webhook_without_secret_skips_check():
    """When credentials are present but no app_secret, skip signature check."""
    provider = TeamsProvider()
    payload = json.dumps(_activity()).encode()
    creds = {"app_id": APP_ID, "app_secret": ""}
    inbound = provider.parse_webhook(payload, {}, credentials=creds)
    assert inbound[0].provider_inbox_id == APP_ID


def test_capabilities_are_honest():
    caps = TeamsProvider.capabilities
    assert Capability.RECEIVE in caps
    assert Capability.REPLY in caps
    assert Capability.SEND in caps
    assert Capability.INITIATE not in caps
    assert Capability.BACKFILL not in caps
