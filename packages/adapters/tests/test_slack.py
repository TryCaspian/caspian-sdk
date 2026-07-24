"""Slack adapter: event normalization, signature verification, app-pool routing."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
from caspian_adapters.base import InboundCommand, InboundReaction, WebhookVerificationError
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


def test_pool_verifies_with_sending_apps_secret():
    pool = [
        {"app_id": "A1", "client_id": "c1", "client_secret": "s1", "signing_secret": "g1"},
        {"app_id": "A2", "client_id": "c2", "client_secret": "s2", "signing_secret": "g2"},
    ]
    provider = SlackProvider(apps=pool)
    payload = json.dumps(_event(app="A2")).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload, secret="g2"))
    assert inbound[0].provider_inbox_id == "A2:T1"


# Reactions


def _reaction_event(
    action="added", reaction="thumbsup", channel="C123", ts="1752000000.0001",
    user="U456", item_type="message", team="T1", app="A1",
):
    return {
        "team_id": team,
        "api_app_id": app,
        "event_id": "Ev2",
        "event": {
            "type": f"reaction_{action}",
            "user": user,
            "reaction": reaction,
            "item": {"type": item_type, "channel": channel, "ts": ts},
            "event_ts": "1752000001.0000",
        },
    }


def test_parse_event_normalizes_a_reaction():
    inbound = parse_event(_reaction_event())
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].emoji == "thumbsup"
    assert inbound[0].action == "added"
    assert inbound[0].sender_address == "U456"
    # points at the message reacted to, so a reply threads on the right ts
    assert inbound[0].provider_message_id == "C123:1752000000.0001"
    assert inbound[0].provider_inbox_id == "A1:T1"


def test_parse_event_marks_a_removed_reaction():
    assert parse_event(_reaction_event(action="removed"))[0].action == "removed"


def test_parse_event_strips_skin_tone_from_reaction():
    # Slack reports a toned reaction as "thumbsup::skin-tone-3"; handlers match the
    # bare shortcode, so the modifier must not leak into the normalized emoji.
    inbound = parse_event(_reaction_event(reaction="thumbsup::skin-tone-3"))
    assert inbound[0].emoji == "thumbsup"


def test_parse_event_skips_reactions_on_non_messages():
    # File reactions have no conversation to route to.
    assert parse_event(_reaction_event(item_type="file")) == []


def test_parse_event_ignores_unknown_event_types():
    # A workspace can subscribe to more events than we handle; that is a no-op,
    # not an error, or every new Slack event type would take the listener down.
    assert parse_event(_event_of_type("app_home_opened")) == []


def _event_of_type(event_type):
    return {"team_id": "T1", "api_app_id": "A1", "event_id": "Ev3",
            "event": {"type": event_type}}


def test_react_adds_the_reaction_without_colons():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    provider = _provider()
    provider._client = httpx.Client(base_url="https://slack.test",
                                    transport=httpx.MockTransport(handler), timeout=5.0)
    provider.react("A1:T1", "C123:1752000000.0001", ":eyes:", credentials={"bot_token": "xoxb-1"})
    assert seen["path"] == "/reactions.add"
    assert seen["body"] == {"channel": "C123", "timestamp": "1752000000.0001", "name": "eyes"}
    assert seen["auth"] == "Bearer xoxb-1"


def test_react_treats_already_reacted_as_success():
    # The desired end state already holds, so a retry must not raise.
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "already_reacted"})

    provider = _provider()
    provider._client = httpx.Client(base_url="https://slack.test",
                                    transport=httpx.MockTransport(handler), timeout=5.0)
    provider.react("A1:T1", "C123:1.0", "eyes", credentials={"bot_token": "xoxb-1"})


def test_react_raises_on_a_real_api_error():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    provider = _provider()
    provider._client = httpx.Client(base_url="https://slack.test",
                                    transport=httpx.MockTransport(handler), timeout=5.0)
    with pytest.raises(RuntimeError, match="channel_not_found"):
        provider.react("A1:T1", "C123:1.0", "eyes", credentials={"bot_token": "xoxb-1"})


# Slash commands (urlencoded, not JSON)


FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}


def _command_body(command="/status", text="prod us-east", channel="C123",
                  channel_name="general", team="T1", app="A1"):
    return urlencode({
        "team_id": team,
        "api_app_id": app,
        "channel_id": channel,
        "channel_name": channel_name,
        "user_id": "U456",
        "user_name": "steve",
        "command": command,
        "text": text,
        "response_url": "https://hooks.slack.test/commands/T1/1",
        "trigger_id": "13345224609.738474920.abc",
    }).encode()


def test_parse_webhook_normalizes_a_slash_command():
    provider = _provider()
    payload = _command_body()
    inbound = provider.parse_webhook(
        payload, {**_signed_headers(payload), **FORM_HEADERS}
    )
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundCommand)
    assert inbound[0].command == "/status"
    assert inbound[0].text == "prod us-east"
    assert inbound[0].provider_thread_id == "C123"
    assert inbound[0].provider_inbox_id == "A1:T1"
    assert inbound[0].sender_name == "steve"
    assert inbound[0].external_event_id == "13345224609.738474920.abc"
    assert inbound[0].response_url == "https://hooks.slack.test/commands/T1/1"


def test_slash_command_is_verified_like_any_other_body():
    # Same v0 signature scheme over the raw bytes, so a form body with a bad
    # signature must be rejected exactly as a JSON one is.
    provider = _provider()
    payload = _command_body()
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        provider.parse_webhook(payload, {**_signed_headers(payload, secret="wrong"),
                                         **FORM_HEADERS})


def test_slash_command_rejects_a_stale_timestamp():
    provider = _provider()
    payload = _command_body()
    old = str(int(time.time()) - MAX_TIMESTAMP_SKEW - 1)
    with pytest.raises(WebhookVerificationError, match="too old"):
        provider.parse_webhook(payload, {**_signed_headers(payload, ts=old), **FORM_HEADERS})


def test_slash_command_marks_a_direct_message():
    # Slack signals a DM by naming the channel, not with a type field.
    provider = _provider()
    payload = _command_body(channel_name="directmessage")
    inbound = provider.parse_webhook(payload, {**_signed_headers(payload), **FORM_HEADERS})
    assert inbound[0].chat_type == "private"


def test_slash_command_without_arguments_has_empty_text():
    provider = _provider()
    payload = _command_body(text="")
    inbound = provider.parse_webhook(payload, {**_signed_headers(payload), **FORM_HEADERS})
    assert inbound[0].text == ""


def test_parse_slash_command_drops_a_body_with_no_command():
    assert parse_slash_command({"team_id": "T1", "text": "hello"}) == []


def test_route_key_reads_a_urlencoded_body():
    # Routing runs before the content type is known, so the form body has to
    # resolve to the same (app, workspace) key as a JSON event.
    assert SlackProvider.route_key(_command_body(team="T9", app="A7")) == "A7:T9"
