"""Slack adapter: event normalization, signature verification, app-pool routing."""

import hashlib
import hmac
import json

import pytest
from caspian_adapters.base import WebhookVerificationError
from caspian_adapters.slack import SlackProvider, parse_event

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


def _signed_headers(payload: bytes, secret=SIGNING_SECRET, ts="12345"):
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


def test_pool_verifies_with_sending_apps_secret():
    pool = [
        {"app_id": "A1", "client_id": "c1", "client_secret": "s1", "signing_secret": "g1"},
        {"app_id": "A2", "client_id": "c2", "client_secret": "s2", "signing_secret": "g2"},
    ]
    provider = SlackProvider(apps=pool)
    payload = json.dumps(_event(app="A2")).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload, secret="g2"))
    assert inbound[0].provider_inbox_id == "A2:T1"


def test_parse_event_normalizes_reaction_added_and_removed():
    from caspian_adapters.base import InboundReaction

    add_payload = {
        "team_id": "T1",
        "api_app_id": "A1",
        "event_id": "EvReaction1",
        "event": {
            "type": "reaction_added",
            "user": "U456",
            "reaction": "thumbsup",
            "item": {
                "type": "message",
                "channel": "C123",
                "ts": "1752000000.0001",
            },
        },
    }
    inbound_add = parse_event(add_payload)
    assert len(inbound_add) == 1
    assert isinstance(inbound_add[0], InboundReaction)
    assert inbound_add[0].emoji == "thumbsup"
    assert inbound_add[0].action == "added"
    assert inbound_add[0].provider_message_id == "C123:1752000000.0001"
    assert inbound_add[0].sender_address == "U456"

    remove_payload = {
        "team_id": "T1",
        "api_app_id": "A1",
        "event_id": "EvReaction2",
        "event": {
            "type": "reaction_removed",
            "user": "U456",
            "reaction": "thumbsup",
            "item": {
                "type": "message",
                "channel": "C123",
                "ts": "1752000000.0001",
            },
        },
    }
    inbound_remove = parse_event(remove_payload)
    assert len(inbound_remove) == 1
    assert isinstance(inbound_remove[0], InboundReaction)
    assert inbound_remove[0].action == "removed"


def test_parse_webhook_normalizes_slash_command():
    from caspian_adapters.base import InboundCommand

    provider = _provider()
    payload = b"command=%2Fweather&text=Chicago&user_id=U123&user_name=tester&channel_id=C456&team_id=T1&api_app_id=A1&trigger_id=trig_1"
    inbound = provider.parse_webhook(payload, _signed_headers(payload))
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundCommand)
    assert inbound[0].command == "weather"
    assert inbound[0].text == "Chicago"
    assert inbound[0].sender_address == "U123"
    assert inbound[0].sender_name == "tester"
    assert inbound[0].provider_thread_id == "C456"


def test_slack_react_calls_api(monkeypatch):
    import httpx

    provider = _provider()
    called = []

    def mock_post(url, json, headers):
        called.append((url, json, headers))
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True}, request=req)

    monkeypatch.setattr(provider._client, "post", mock_post)
    provider.react(
        provider_inbox_id="A1:T1",
        provider_message_id="C123:1752000000.0001",
        emoji=":thumbsup:",
        credentials={"bot_token": "token-1"},
    )
    assert len(called) == 1
    assert called[0][0] == "/reactions.add"
    assert called[0][1] == {"channel": "C123", "timestamp": "1752000000.0001", "name": "thumbsup"}
    assert called[0][2] == {"Authorization": "Bearer token-1"}

