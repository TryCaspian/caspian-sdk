"""Reddit modmail: normalize conversations + check webhook secret."""

import json

import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.config import Settings
from caspian_adapters.fake_reddit import FakeRedditProvider
from caspian_adapters.reddit import RedditProvider, parse_conversation
from caspian_adapters.registry import build_providers

CREDS = {
    "access_token": "reddit-oauth-token",
    "subreddit": "testsub",
    "webhook_secret": "shh",
}


def _conv(text="please unban me", conversation_id="abc123", message_id="msg1",
          internal=False, author="someuser"):
    return {
        "conversation": {
            "id": conversation_id,
            "subject": "ban appeal",
            "isInternal": internal,
            "state": 0,
            "numMessages": 1,
            "objIds": [{"id": message_id, "key": "messages"}],
            "participant": {"name": author, "id": "t2_user1", "isMod": False},
            "owner": {"displayName": "testsub", "type": "subreddit", "id": "t5_test"},
        },
        "messages": {
            message_id: {
                "id": message_id,
                "author": {"name": author, "id": "t2_user1", "isMod": False},
                "body": f"<div>{text}</div>",
                "bodyMarkdown": text,
                "date": "2024-06-01T12:00:00.000000+00:00",
            }
        },
        "modActions": {},
    }


def test_parse_conversation_normalizes():
    inbound = parse_conversation(_conv(), "r/testsub")
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "please unban me"
    assert msg.subject == "ban appeal"
    assert msg.provider_inbox_id == "r/testsub"
    assert msg.provider_message_id == "abc123:msg1"
    assert msg.provider_thread_id == "abc123"
    assert msg.sender_address == "someuser"
    assert msg.chat_type == "modmail"


def test_parse_conversation_skips_junk():
    assert parse_conversation(_conv(internal=True), "r/testsub") == []
    assert parse_conversation({"conversation": {"id": "x"}}, "r/testsub") == []
    assert parse_conversation({}, "r/testsub") == []


def test_parse_conversation_takes_latest():
    data = _conv(text="first", message_id="m1")
    data["conversation"]["objIds"] = [
        {"id": "m1", "key": "messages"},
        {"id": "m2", "key": "messages"},
    ]
    data["messages"]["m2"] = {
        "id": "m2",
        "author": {"name": "someuser", "id": "t2_user1"},
        "bodyMarkdown": "second",
        "body": "<div>second</div>",
    }
    inbound = parse_conversation(data, "r/testsub")
    assert inbound[0].text == "second"
    assert inbound[0].provider_message_id == "abc123:m2"


def test_parse_webhook_needs_scope():
    provider = RedditProvider()
    with pytest.raises(WebhookVerificationError, match="connection scope"):
        provider.parse_webhook(b"{}", {}, credentials=None)


def test_parse_webhook_checks_secret():
    provider = RedditProvider()
    payload = json.dumps(_conv()).encode()
    inbound = provider.parse_webhook(
        payload, {"X-Caspian-Webhook-Secret": "shh"}, credentials=CREDS
    )
    assert inbound[0].text == "please unban me"
    with pytest.raises(WebhookVerificationError, match="secret token"):
        provider.parse_webhook(
            payload, {"X-Caspian-Webhook-Secret": "wrong"}, credentials=CREDS
        )
    with pytest.raises(WebhookVerificationError, match="secret token"):
        provider.parse_webhook(payload, {}, credentials=CREDS)


def test_parse_webhook_no_secret_ok():
    provider = RedditProvider()
    payload = json.dumps(_conv()).encode()
    inbound = provider.parse_webhook(
        payload, {}, credentials={"access_token": "tok", "subreddit": "testsub"}
    )
    assert inbound[0].provider_inbox_id == "r/testsub"


def test_fake_send_reply():
    fake = FakeRedditProvider()
    sent = fake.send("r/testsub", OutboundMessage(to=("abc123",), text="hi"))
    assert sent.provider_thread_id == "abc123"
    assert fake.sent[0]["text"] == "hi"

    replied = fake.reply("r/testsub", "abc123:msg1", OutboundMessage(text="ok"))
    assert replied.provider_thread_id == "abc123"
    assert fake.replies[0]["in_reply_to"] == "msg1"


def test_fake_roundtrip():
    fake = FakeRedditProvider(webhook_secret="shh")
    body = fake.webhook_payload(text="help")
    inbound = fake.parse_webhook(
        json.dumps(body).encode(),
        {"X-Caspian-Webhook-Secret": "shh"},
        credentials={"subreddit": "testsub", "webhook_secret": "shh"},
    )
    assert inbound[0].text == "help"


def test_registry_fake_reddit():
    providers = build_providers(Settings(providers="fake-reddit"))
    assert providers["fake-reddit"].channel == "reddit"
