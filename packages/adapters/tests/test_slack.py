"""Slack adapter: event normalization, signature verification, app-pool routing."""

import hashlib
import hmac
import json
import time

import pytest
from caspian_adapters.base import (
    InboundCommand,
    InboundReaction,
    WebhookVerificationError,
)
from caspian_adapters.slack import (
    MAX_TIMESTAMP_SKEW,
    SlackProvider,
    parse_event,
    parse_slash_command,
)

SIGNING_SECRET = "sign-me"


def _provider(**kwargs) -> SlackProvider:
    defaults = {
        "client_id": "client-1",
        "client_secret": "secret-1",
        "signing_secret": SIGNING_SECRET,
    }
    defaults.update(kwargs)
    return SlackProvider(**defaults)


def _event(text="hello", channel="C123", user="U456", team="T1", app="A1", **event_extra):
    return {
        "team_id": team,
        "api_app_id": app,
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel": channel,
            "user": user,
            "text": text,
            "ts": "1752000000.0001",
            "channel_type": "channel",
            **event_extra,
        },
    }


def _signed_headers(payload: bytes, secret=SIGNING_SECRET, ts=None):
    # Default to a current timestamp so the signature passes the recency check;
    # pass an explicit ts to exercise stale/invalid-timestamp rejection.
    if ts is None:
        ts = str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + payload
    sig = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def test_parse_event_normalizes_user_message():
    inbound = parse_event(_event())
    assert len(inbound) == 1
    assert inbound[0].text == "hello"
    assert inbound[0].sender_address == "U456"
    assert inbound[0].provider_inbox_id == "A1:T1"  # routed by app + workspace
    assert inbound[0].provider_thread_id == "C123"


def test_parse_event_skips_bot_and_subtype_messages():
    assert parse_event(_event(bot_id="B99")) == []
    assert parse_event(_event(subtype="message_changed")) == []


def test_parse_webhook_accepts_valid_signature():
    provider = _provider()
    payload = json.dumps(_event()).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload))
    assert inbound[0].text == "hello"


def test_parse_webhook_rejects_bad_signature():
    provider = _provider()
    payload = json.dumps(_event()).encode()
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(payload, _signed_headers(payload, secret="wrong"))


def test_parse_webhook_rejects_stale_timestamp():
    # A correctly-signed request is still rejected once its timestamp is older
    # than the allowed skew, so a captured request can't be replayed later.
    provider = _provider()
    payload = json.dumps(_event()).encode()
    stale_ts = str(int(time.time()) - MAX_TIMESTAMP_SKEW - 60)
    with pytest.raises(WebhookVerificationError, match="too old"):
        provider.parse_webhook(payload, _signed_headers(payload, ts=stale_ts))


@pytest.mark.parametrize("ts", ["", "not-a-timestamp"])
def test_parse_webhook_rejects_missing_or_invalid_timestamp(ts):
    provider = _provider()
    payload = json.dumps(_event()).encode()
    headers = _signed_headers(payload, ts=ts)
    with pytest.raises(WebhookVerificationError, match="missing or invalid"):
        provider.parse_webhook(payload, headers)


def test_url_verification_returns_no_messages():
    provider = _provider()
    payload = json.dumps({"type": "url_verification", "challenge": "xyz"}).encode()
    assert provider.parse_webhook(payload, _signed_headers(payload)) == []


def test_route_key_is_app_and_team():
    payload = json.dumps(_event(team="T9", app="A7")).encode()
    assert SlackProvider.route_key(payload) == "A7:T9"
    assert SlackProvider.route_key(b"not json") is None
    assert SlackProvider.route_key(b"{}") is None


def test_app_pool_selection():
    pool = [
        {"app_id": "A1", "client_id": "c1", "client_secret": "s1", "signing_secret": "g1"},
        {"app_id": "A2", "client_id": "c2", "client_secret": "s2", "signing_secret": "g2"},
    ]
    provider = SlackProvider(apps=pool)
    assert provider.pool_size() == 2
    assert provider.client_id == "c1"
    assert provider.app_at(1)["app_id"] == "A2"
    assert provider.app_at(99)["app_id"] == "A2"  # clamps to last


def test_default_scopes_include_reaction_read_and_write():
    provider = SlackProvider(client_id="c1")
    assert "reactions:read" in provider.scopes
    assert "reactions:write" in provider.scopes


def test_pool_verifies_with_sending_apps_secret():
    pool = [
        {"app_id": "A1", "client_id": "c1", "client_secret": "s1", "signing_secret": "g1"},
        {"app_id": "A2", "client_id": "c2", "client_secret": "s2", "signing_secret": "g2"},
    ]
    provider = SlackProvider(apps=pool)
    payload = json.dumps(_event(app="A2")).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload, secret="g2"))
    assert inbound[0].provider_inbox_id == "A2:T1"


# --- Reactions ---


def _reaction_event(emoji="thumbsup", action="reaction_added", user="U456",
                    channel="C123", message_ts="1752000000.0001", team="T1", app="A1"):
    return {
        "team_id": team,
        "api_app_id": app,
        "event_id": "EvR1",
        "event": {
            "type": action,
            "user": user,
            "reaction": emoji,
            "item": {"type": "message", "channel": channel, "ts": message_ts},
        },
    }


def test_parse_event_reaction_added():
    inbound = parse_event(_reaction_event(action="reaction_added"))
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].emoji == "thumbsup"
    assert inbound[0].action == "added"
    assert inbound[0].source_provider_message_id == "C123:1752000000.0001"
    assert inbound[0].sender_address == "U456"
    assert inbound[0].provider_inbox_id == "A1:T1"


def test_parse_event_reaction_removed():
    inbound = parse_event(_reaction_event(action="reaction_removed"))
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].action == "removed"


def test_parse_webhook_reaction_with_signature():
    provider = _provider()
    payload = json.dumps(_reaction_event()).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload))
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].emoji == "thumbsup"


# --- Slash commands ---


def _slash_command_payload(command="/deploy", text="staging", user="U456",
                           channel="C123", team="T1", app="A1"):
    return {
        "team_id": team,
        "api_app_id": app,
        "command": command,
        "text": text,
        "user_id": user,
        "channel_id": channel,
        "channel_type": "channel",
        "trigger_id": "trigger_123",
    }


def test_parse_slash_command_normalizes():
    inbound = parse_slash_command(_slash_command_payload())
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundCommand)
    assert inbound[0].command == "/deploy"
    assert inbound[0].args == "staging"
    assert inbound[0].text == "/deploy staging"
    assert inbound[0].sender_address == "U456"
    assert inbound[0].provider_inbox_id == "A1:T1"
    assert inbound[0].provider_thread_id == "C123"


def test_parse_slash_command_no_args():
    inbound = parse_slash_command(_slash_command_payload(command="/start", text=""))
    assert inbound[0].command == "/start"
    assert inbound[0].args is None
    assert inbound[0].text == "/start"


def test_parse_slash_command_empty():
    assert parse_slash_command({}) == []


def test_parse_webhook_slash_command():
    provider = _provider()
    payload = json.dumps(_slash_command_payload()).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload))
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundCommand)
    assert inbound[0].command == "/deploy"


def test_parse_webhook_slash_command_bad_signature():
    provider = _provider()
    payload = json.dumps(_slash_command_payload()).encode()
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(payload, _signed_headers(payload, secret="wrong"))
