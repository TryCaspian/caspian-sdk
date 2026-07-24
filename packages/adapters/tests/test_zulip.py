"""Zulip adapter: payload normalization, token verification, and send/reply routing."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
from caspian_adapters.base import OutboundMessage, ProvisionRequest, WebhookVerificationError
from caspian_adapters.fake_zulip import FakeZulipProvider
from caspian_adapters.zulip import (
    ZulipProvider,
    _destination,
    encode_dm,
    encode_stream,
    parse_message,
)

SITE = "https://acme.zulipchat.com"
BOT_EMAIL = "agent-bot@acme.zulipchat.com"
TOKEN = "outgoing-webhook-token"

CREDS = {"site": SITE, "bot_email": BOT_EMAIL, "api_key": "key-123", "webhook_token": TOKEN}


def _stream_payload(text="@**Agent** help", stream_id=7, topic="support", sender="c@acme.com"):
    return {
        "bot_email": BOT_EMAIL,
        "token": TOKEN,
        "trigger": "mention",
        "message": {
            "id": 501,
            "type": "stream",
            "content": text,
            "sender_id": 55,
            "sender_email": sender,
            "sender_full_name": "Casey Customer",
            "display_recipient": "general",
            "stream_id": stream_id,
            "subject": topic,
        },
    }


def _dm_payload(text="hi bot", others=None):
    others = others or [{"id": 55, "email": "c@acme.com", "full_name": "Casey"}]
    return {
        "bot_email": BOT_EMAIL,
        "token": TOKEN,
        "trigger": "direct_message",
        "message": {
            "id": 777,
            "type": "private",
            "content": text,
            "sender_id": others[0]["id"],
            "sender_email": others[0]["email"],
            "sender_full_name": others[0]["full_name"],
            "display_recipient": [
                *others,
                {"id": 9001, "email": BOT_EMAIL, "full_name": "Agent"},
            ],
        },
    }


# --- normalization ---------------------------------------------------------


def test_parse_stream_message():
    [msg] = parse_message(_stream_payload())
    assert msg.text == "@**Agent** help"
    assert msg.provider_inbox_id == BOT_EMAIL
    assert msg.provider_thread_id == "stream:7:support"
    assert msg.provider_message_id == "stream:7:support:501"
    assert msg.sender_address == "c@acme.com"
    assert msg.sender_name == "Casey Customer"
    assert msg.chat_type == "channel"
    assert msg.external_event_id == "501"


def test_parse_dm_excludes_the_bot_and_marks_private():
    [msg] = parse_message(_dm_payload())
    # Thread is the set of *other* humans (bot's own id/email dropped).
    assert msg.provider_thread_id == "dm:55"
    assert msg.provider_message_id == "dm:55:777"
    assert msg.chat_type == "private"
    assert msg.recipients == [{"id": 55, "email": "c@acme.com", "full_name": "Casey"}]


def test_parse_group_dm_marks_group_and_sorts_ids():
    others = [
        {"id": 88, "email": "b@acme.com", "full_name": "Bo"},
        {"id": 55, "email": "c@acme.com", "full_name": "Casey"},
    ]
    [msg] = parse_message(_dm_payload(others=others))
    assert msg.provider_thread_id == "dm:55,88"  # sorted
    assert msg.chat_type == "group"


def test_parse_skips_empty_and_own_messages():
    assert parse_message(_stream_payload(text="")) == []
    own = _stream_payload()
    own["message"]["sender_email"] = BOT_EMAIL  # the bot's own post
    assert parse_message(own) == []
    assert parse_message({}) == []


def test_parse_skips_unknown_message_types():
    # A future Zulip type (or partial payload) must be dropped, not guessed at
    # as a DM shape.
    unknown = _stream_payload()
    unknown["message"]["type"] = "channel_event"
    assert parse_message(unknown) == []


def test_topic_with_colon_round_trips():
    # Free-form topics may contain ':'; url-quoting keeps the composite id splittable.
    routing = encode_stream(7, "deploy: prod")
    assert ":" not in routing.split(":", 2)[2]  # topic segment carries no raw colon
    thread, fields = _destination(f"{routing}:999")
    assert thread == routing
    assert fields == {"type": "stream", "to": 7, "topic": "deploy: prod"}


def test_destination_decodes_dm_and_rejects_garbage():
    assert _destination("dm:55,88:777")[1] == {"type": "direct", "to": json.dumps([55, 88])}
    assert _destination(encode_dm([88, 55]))[0] == "dm:55,88"
    assert _destination("dm:88,55")[0] == "dm:55,88"  # canonicalized on decode too
    with pytest.raises(ValueError, match="unroutable"):
        _destination("carrier-pigeon:nope")


def test_destination_rejects_extra_segments():
    # One trailing message id is allowed; anything beyond that is malformed and
    # must error instead of silently mis-routing.
    with pytest.raises(ValueError, match="unroutable"):
        _destination("stream:7:topic:501:extra")
    with pytest.raises(ValueError, match="unroutable"):
        _destination("dm:55,88:777:extra")


# --- webhook verification --------------------------------------------------


def test_parse_webhook_accepts_matching_token():
    provider = ZulipProvider()
    payload = json.dumps(_stream_payload()).encode()
    [msg] = provider.parse_webhook(payload, {}, credentials=CREDS)
    assert msg.text == "@**Agent** help"


def test_parse_webhook_rejects_bad_token():
    provider = ZulipProvider()
    payload = json.dumps(_stream_payload()).encode()
    with pytest.raises(WebhookVerificationError, match="token mismatch"):
        provider.parse_webhook(payload, {}, credentials={**CREDS, "webhook_token": "wrong"})


def test_parse_webhook_rejects_bad_json_and_missing_scope():
    provider = ZulipProvider()
    with pytest.raises(WebhookVerificationError, match="invalid JSON"):
        provider.parse_webhook(b"not json", {}, credentials=CREDS)
    with pytest.raises(WebhookVerificationError, match="connection scope"):
        provider.parse_webhook(b"{}", {}, credentials=None)


def test_parse_webhook_fails_closed_without_a_token_credential():
    # Zulip always stamps its POSTs with the bot's token, so a connection with
    # no stored token can never verify inbound - reject, don't skip the check.
    provider = ZulipProvider()
    payload = json.dumps(_stream_payload()).encode()
    with pytest.raises(WebhookVerificationError, match="webhook_token"):
        provider.parse_webhook(payload, {}, credentials={**CREDS, "webhook_token": ""})


# --- send / reply (mocked Zulip REST API) ----------------------------------


def _mock_provider(handler) -> ZulipProvider:
    provider = ZulipProvider()
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def _capture(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"result": "success", "id": 12345})

    return handler


def test_send_to_stream_posts_with_topic_and_basic_auth():
    seen: dict = {}
    provider = _mock_provider(_capture(seen))
    result = provider.send(
        "inbox",
        OutboundMessage(text="on it", to=("stream:7:support",)),
        credentials=CREDS,
    )
    assert seen["url"] == f"{SITE}/api/v1/messages"
    assert seen["form"] == {"type": "stream", "to": "7", "topic": "support", "content": "on it"}
    assert seen["auth"].startswith("Basic ")  # bot_email:api_key
    assert result.provider_message_id == "stream:7:support:12345"
    assert result.provider_thread_id == "stream:7:support"


def test_reply_routes_to_dm_recipients():
    seen: dict = {}
    provider = _mock_provider(_capture(seen))
    result = provider.reply(
        "inbox",
        "dm:55,88:777",
        OutboundMessage(text="thanks"),
        credentials=CREDS,
    )
    assert seen["form"]["type"] == "direct"
    assert json.loads(seen["form"]["to"]) == [55, 88]
    assert seen["form"]["content"] == "thanks"
    assert result.provider_thread_id == "dm:55,88"


def test_provision_resolves_bot_user_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/users/me"
        return httpx.Response(200, json={"result": "success", "user_id": 9001})

    provider = _mock_provider(handler)
    result = provider.provision(ProvisionRequest(
        connection_id="c1", customer_id="cust", agent_id="a1", credentials=CREDS
    ))
    assert result.address == BOT_EMAIL
    assert result.provider_resource_id == "9001"


# --- offline fake ----------------------------------------------------------


def test_fake_round_trips_webhook_and_send():
    fake = FakeZulipProvider()
    [msg] = fake.parse_webhook(json.dumps(fake.webhook_payload()).encode(), {})
    assert msg.chat_type == "channel"
    assert msg.provider_thread_id.startswith("stream:7:")

    reply = fake.reply("inbox", msg.provider_message_id, OutboundMessage(text="hello"), None)
    assert reply.provider_thread_id == msg.provider_thread_id
    assert fake.replies[-1]["fields"]["type"] == "stream"


def test_fake_rejects_bad_token():
    fake = FakeZulipProvider()
    bad = fake.webhook_payload()
    bad["token"] = "nope"
    with pytest.raises(WebhookVerificationError):
        fake.parse_webhook(json.dumps(bad).encode(), {})


# --- registry --------------------------------------------------------------


def test_registry_builds_zulip_and_fake():
    from caspian_adapters import Settings, build_providers

    providers = build_providers(Settings(
        providers="zulip,fake-zulip",
        zulip_site=SITE, zulip_bot_email=BOT_EMAIL,
        zulip_api_key="key-123", zulip_webhook_token=TOKEN,
    ))
    assert providers["zulip"].channel == "zulip"
    assert providers["fake-zulip"].channel == "zulip"
    assert "receive" in providers["zulip"].capabilities
