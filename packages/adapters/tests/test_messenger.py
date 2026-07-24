"""Instagram DM / Facebook Messenger adapters (Meta Graph API)."""

import hashlib
import hmac
import json

import httpx
import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.messenger import FacebookProvider, InstagramProvider

PAGE_ID = "200123"
APP_SECRET = "app-secret"


def _provider(handler=None, cls=InstagramProvider):
    provider = cls(
        page_id=PAGE_ID,
        access_token="page-token",
        app_secret=APP_SECRET,
        verify_token="verify-me",
    )
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(200, json={})))
    provider._client = httpx.Client(
        transport=transport,
        base_url="https://graph.test",
        headers={"Authorization": "Bearer page-token"},
    )
    return provider


def _webhook(channel: str, sender="777", text="hello"):
    return json.dumps(
        {
            "object": channel,
            "entry": [
                {
                    "id": PAGE_ID,
                    "messaging": [
                        {
                            "sender": {"id": sender},
                            "recipient": {"id": PAGE_ID},
                            "message": {"mid": "mid.1", "text": text},
                        }
                    ],
                }
            ],
        }
    ).encode()


def _signature(payload: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def test_requires_page_and_token():
    with pytest.raises(ValueError):
        InstagramProvider(page_id="", access_token="")


def test_send_posts_to_graph_messages():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message_id": "m.123"})

    provider = _provider(handler)
    result = provider.send(PAGE_ID, OutboundMessage(to=["777"], text="hi there"))
    assert seen["path"] == f"/{PAGE_ID}/messages"
    assert seen["body"]["recipient"] == {"id": "777"}
    assert seen["body"]["message"]["text"] == "hi there"
    assert result.provider_message_id == "777:m.123"
    assert result.provider_thread_id == "777"


def test_reply_targets_original_sender():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message_id": "m.456"})

    provider = _provider(handler)
    result = provider.reply(PAGE_ID, "777:mid.1", OutboundMessage(to=[], text="answer"))
    assert result.provider_thread_id == "777"


def test_parse_webhook_verifies_signature_and_normalizes():
    provider = _provider()
    payload = _webhook("instagram")
    inbound = provider.parse_webhook(payload, {"X-Hub-Signature-256": _signature(payload)})
    assert len(inbound) == 1
    assert inbound[0].text == "hello"
    assert inbound[0].sender_address == "777"
    assert inbound[0].provider_thread_id == "777"
    assert inbound[0].chat_type == "instagram"


def test_parse_webhook_rejects_bad_signature():
    provider = _provider()
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(_webhook("instagram"), {"x-hub-signature-256": "sha256=bad"})


def test_echo_and_textless_events_skipped():
    provider = _provider()
    payload = json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": PAGE_ID,
                    "messaging": [
                        {
                            "sender": {"id": "777"},
                            "message": {"mid": "m1", "text": "hi", "is_echo": True},
                        },
                        {"sender": {"id": "777"}, "message": {"mid": "m2"}},
                    ],
                }
            ],
        }
    ).encode()
    inbound = provider.parse_webhook(payload, {"x-hub-signature-256": _signature(payload)})
    assert inbound == []


def test_hub_challenge_echo():
    provider = _provider()
    ok = provider.meta_verify(
        {
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "999",
        }
    )
    assert ok == "999"
    assert (
        provider.meta_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "999",
            }
        )
        is None
    )


def test_hub_challenge_rejects_when_verify_token_unset():
    # A provider with no configured verify_token must not echo the challenge,
    # even for an empty incoming token (would otherwise fail open).
    provider = InstagramProvider(page_id=PAGE_ID, access_token="page-token")
    assert (
        provider.meta_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "",
                "hub.challenge": "999",
            }
        )
        is None
    )


def test_facebook_provider_same_shape():
    provider = _provider(cls=FacebookProvider)
    assert provider.channel == "facebook"
    assert provider.name == "facebook"
    assert provider.provision(None).provider_resource_id == PAGE_ID
