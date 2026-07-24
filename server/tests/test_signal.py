"""Signal adapter: normalization, verification accept/reject, routing, and fake."""

import json

import httpx
import pytest
from caspian_adapters import Settings, build_providers
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.signal import SignalProvider, parse_envelope

NUMBER = "+15559876543"


def _envelope(text="hi signal", sender="+15551112222", timestamp=1752400000000, group_id=None):
    data_msg: dict = {"timestamp": timestamp, "message": text}
    if group_id:
        data_msg["groupInfo"] = {"groupId": group_id, "type": "DELIVER"}
    return {
        "envelope": {
            "source": sender,
            "sourceNumber": sender,
            "sourceName": "Bob",
            "timestamp": timestamp,
            "dataMessage": data_msg,
        },
        "account": NUMBER,
    }


def test_parse_envelope_normalizes_private_message():
    inbound = parse_envelope(_envelope(), local_number=NUMBER)
    assert len(inbound) == 1
    assert inbound[0].text == "hi signal"
    assert inbound[0].provider_inbox_id == NUMBER
    assert inbound[0].provider_message_id == "+15551112222:1752400000000"
    assert inbound[0].provider_thread_id == "+15551112222"
    assert inbound[0].sender_address == "+15551112222"
    assert inbound[0].sender_name == "Bob"
    assert inbound[0].chat_type == "private"


def test_parse_envelope_normalizes_group_message():
    inbound = parse_envelope(_envelope(group_id="group123"), local_number=NUMBER)
    assert len(inbound) == 1
    assert inbound[0].chat_type == "group"
    assert inbound[0].provider_thread_id == "group:group123"
    assert inbound[0].provider_message_id == "group:group123:1752400000000"


def test_parse_envelope_ignores_self_message():
    assert parse_envelope(_envelope(sender=NUMBER), local_number=NUMBER) == []


def test_parse_envelope_ignores_sync_message():
    data = _envelope()
    data["envelope"]["syncMessage"] = {}
    assert parse_envelope(data, local_number=NUMBER) == []


def test_parse_webhook_enforces_secret_header():
    provider = SignalProvider(number=NUMBER, webhook_secret="shh")
    payload = json.dumps(_envelope()).encode()
    inbound = provider.parse_webhook(
        payload, {"X-Signal-Secret-Token": "shh"}, credentials={"number": NUMBER}
    )
    assert len(inbound) == 1
    assert inbound[0].text == "hi signal"

    with pytest.raises(WebhookVerificationError, match="secret token mismatch"):
        provider.parse_webhook(
            payload, {"X-Signal-Secret-Token": "wrong"}, credentials={"number": NUMBER}
        )

    with pytest.raises(WebhookVerificationError, match="invalid JSON"):
        provider.parse_webhook(
            b"invalid json", {"X-Signal-Secret-Token": "shh"}, credentials={"number": NUMBER}
        )


def _mock_provider(handler):
    provider = SignalProvider(number=NUMBER, daemon_url="http://127.0.0.1:8080")
    provider._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8080"
    )
    return provider


def test_send_direct_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": {"timestamp": 1752400001000}, "id": "1"}
        )

    provider = _mock_provider(handler)
    msg = OutboundMessage(text="hello", to=("+15553334444",))
    res = provider.send(NUMBER, msg, credentials={"number": NUMBER})

    assert seen["path"] == "/api/v1/rpc"
    assert seen["body"]["method"] == "send"
    assert seen["body"]["params"]["recipient"] == ["+15553334444"]
    assert seen["body"]["params"]["message"] == "hello"
    assert res.provider_message_id == "+15553334444:1752400001000"


def test_reply_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": {"timestamp": 1752400002000}, "id": "1"}
        )

    provider = _mock_provider(handler)
    msg = OutboundMessage(text="replying")
    res = provider.reply(NUMBER, "+15553334444:1752400000000", msg, credentials={"number": NUMBER})

    assert seen["body"]["params"]["recipient"] == ["+15553334444"]
    assert seen["body"]["params"]["quoteTimestamp"] == 1752400000000
    assert seen["body"]["params"]["quoteAuthor"] == "+15553334444"
    assert res.provider_message_id == "+15553334444:1752400002000"


def test_fake_signal_provider():
    providers = build_providers(Settings(providers="fake-signal"))
    fake = providers["fake-signal"]
    assert fake.channel == "signal"
    payload = json.dumps(fake.webhook_payload(text="fake msg")).encode()
    inbound = fake.parse_webhook(payload, {})
    assert inbound[0].text == "fake msg"

    res = fake.send("inbox", OutboundMessage(text="outgoing", to=("+15557778888",)))
    assert len(fake.sent) == 1
    assert fake.sent[0]["to"] == "+15557778888"
    assert res.provider_thread_id == "+15557778888"
