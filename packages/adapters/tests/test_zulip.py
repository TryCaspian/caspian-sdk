import json

import pytest
from caspian_adapters.base import WebhookVerificationError
from caspian_adapters.zulip import ZulipProvider, parse_event


def test_provider_metadata():
    provider = ZulipProvider()

    assert provider.name == "zulip"
    assert provider.channel == "zulip"


def test_parse_event_stream_message():
    events = parse_event(
        {
            "id": 123,
            "message_type": "stream",
            "stream_id": 42,
            "sender_email": "alice@example.com",
            "sender_full_name": "Alice",
            "content": "hello",
            "type": "message",
        },
        "bot@example.com",
    )

    assert len(events) == 1
    assert events[0].provider_inbox_id == "bot@example.com"
    assert events[0].provider_thread_id == "42"
    assert events[0].chat_type == "stream"


def test_parse_event_private_message():
    events = parse_event(
        {
            "id": 456,
            "message_type": "private",
            "recipient_id": 99,
            "sender_email": "bob@example.com",
            "sender_full_name": "Bob",
            "content": "hi",
            "type": "message",
        },
        "bot@example.com",
    )

    assert len(events) == 1
    assert events[0].provider_thread_id == "99"
    assert events[0].chat_type == "private"
def test_parse_webhook_accepts_valid_secret():
    provider = ZulipProvider()

    events = provider.parse_webhook(
        payload=json.dumps(
            {
                "id": 1,
                "type": "message",
                "message_type": "private",
                "recipient_id": 10,
                "sender_email": "alice@example.com",
                "sender_full_name": "Alice",
                "content": "hello",
            }
        ).encode(),
        headers={"x-zulip-webhook-secret": "secret"},
        credentials={
            "email": "bot@example.com",
            "webhook_secret": "secret",
        },
    )

    assert len(events) == 1
    assert events[0].text == "hello"


def test_parse_webhook_rejects_invalid_secret():
    provider = ZulipProvider()

    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(
            payload=b"{}",
            headers={"x-zulip-webhook-secret": "wrong"},
            credentials={
                "email": "bot@example.com",
                "webhook_secret": "secret",
            },
        )