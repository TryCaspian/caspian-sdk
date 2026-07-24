"""Signal adapter tests: normalization, webhook, sending, and fake provider."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from caspian_adapters.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from caspian_adapters.signal import FakeSignalProvider, SignalProvider

SENDER = "+33123456789"
ACCOUNT = "+1234567890"


def test_parse_webhook_normalizes_message_private():
    provider = SignalProvider(number=ACCOUNT)
    fake = FakeSignalProvider(number=ACCOUNT)
    payload = json.dumps(
        fake.webhook_payload(sender=SENDER, sender_name="Alice", text="hello bot")
    ).encode()

    inbound = provider.parse_webhook(payload, {})
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "hello bot"
    assert msg.sender_address == SENDER
    assert msg.sender_name == "Alice"
    assert msg.provider_inbox_id == ACCOUNT
    assert msg.provider_thread_id == SENDER
    assert msg.chat_type == "private"
    assert msg.provider_message_id.startswith(f"{SENDER}:")


def test_parse_webhook_normalizes_message_group():
    provider = SignalProvider(number=ACCOUNT)
    fake = FakeSignalProvider(number=ACCOUNT)
    payload = json.dumps(
        fake.webhook_payload(
            sender=SENDER, sender_name="Alice", text="hello group", group_id="group.abc"
        )
    ).encode()

    inbound = provider.parse_webhook(payload, {})
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "hello group"
    assert msg.sender_address == SENDER
    assert msg.sender_name == "Alice"
    assert msg.provider_inbox_id == ACCOUNT
    assert msg.provider_thread_id == "group.abc"
    assert msg.chat_type == "group"
    assert msg.provider_message_id.startswith("group.abc:")


def test_parse_webhook_token_verification():
    provider = SignalProvider(number=ACCOUNT, webhook_secret="shh")
    fake = FakeSignalProvider(number=ACCOUNT)
    payload = json.dumps(fake.webhook_payload(text="verified")).encode()

    # Success case
    inbound = provider.parse_webhook(payload, {"x-signal-webhook-token": "shh"})
    assert len(inbound) == 1

    # Failure case
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(payload, {"x-signal-webhook-token": "wrong"})

    # Missing header case
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(payload, {})


def test_fake_signal_provider():
    fake = FakeSignalProvider(number=ACCOUNT)

    # provision
    res = fake.provision(ProvisionRequest("c1", "cust", "agt"))
    assert res.address == ACCOUNT
    assert res.provider_resource_id == ACCOUNT

    # send
    send_res = fake.send(ACCOUNT, OutboundMessage(text="hello", to=(SENDER,)))
    assert len(fake.sent) == 1
    assert fake.sent[0]["recipient"] == SENDER
    assert fake.sent[0]["message"] == "hello"
    assert send_res.provider_thread_id == SENDER

    # reply
    reply_res = fake.reply(
        ACCOUNT,
        f"{SENDER}:1631458508784:{SENDER}",
        OutboundMessage(text="hi"),
    )
    assert len(fake.replies) == 1
    assert fake.replies[0]["recipient"] == SENDER
    assert fake.replies[0]["message"] == "hi"
    assert fake.replies[0]["quote"]["timestamp"] == "1631458508784"
    assert fake.replies[0]["quote"]["author"] == SENDER
    assert reply_res.provider_thread_id == SENDER


def test_signal_provider_send_http():
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["method"] == "send"
        assert body["params"]["recipient"] == [SENDER]
        assert body["params"]["message"] == "hello HTTP"
        assert body["params"]["account"] == ACCOUNT
        resp = {"jsonrpc": "2.0", "result": {"timestamp": 12345}, "id": "1"}
        return httpx.Response(200, json=resp)

    provider = SignalProvider(number=ACCOUNT, http_url="http://local-signal/json-rpc")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))

    res = provider.send(ACCOUNT, OutboundMessage(text="hello HTTP", to=(SENDER,)))
    assert len(calls) == 1
    assert res.provider_message_id == f"{SENDER}:12345:{ACCOUNT}"
    assert res.provider_thread_id == SENDER


@patch("socket.socket")
def test_signal_provider_send_unix_socket(mock_socket_class):
    mock_socket = MagicMock()
    mock_socket_class.return_value.__enter__.return_value = mock_socket

    # Mock socket receive response
    res_data = {"jsonrpc": "2.0", "result": {"timestamp": 54321}, "id": "1"}
    response_payload = json.dumps(res_data) + "\n"
    mock_socket.recv.side_effect = [response_payload.encode("utf-8"), b""]

    provider = SignalProvider(number=ACCOUNT, socket_path="/var/run/signal.sock")
    res = provider.send(ACCOUNT, OutboundMessage(text="hello socket", to=(SENDER,)))

    # Verify socket connect and write
    mock_socket.connect.assert_called_with("/var/run/signal.sock")
    written = b"".join(call[0][0] for call in mock_socket.sendall.call_args_list)
    request_data = json.loads(written.decode("utf-8").strip())
    assert request_data["method"] == "send"
    assert request_data["params"]["message"] == "hello socket"
    assert request_data["params"]["recipient"] == [SENDER]

    # Verify SendResult
    assert res.provider_message_id == f"{SENDER}:54321:{ACCOUNT}"
    assert res.provider_thread_id == SENDER


@patch("socket.socket")
def test_signal_provider_send_tcp_socket(mock_socket_class):
    mock_socket = MagicMock()
    mock_socket_class.return_value.__enter__.return_value = mock_socket

    res_data = {"jsonrpc": "2.0", "result": {"timestamp": 98765}, "id": "1"}
    response_payload = json.dumps(res_data) + "\n"
    mock_socket.recv.side_effect = [response_payload.encode("utf-8"), b""]

    provider = SignalProvider(number=ACCOUNT, tcp_address="127.0.0.1:7583")
    res = provider.send(ACCOUNT, OutboundMessage(text="hello tcp", to=(SENDER,)))

    mock_socket.connect.assert_called_with(("127.0.0.1", 7583))
    written = b"".join(call[0][0] for call in mock_socket.sendall.call_args_list)
    request_data = json.loads(written.decode("utf-8").strip())
    assert request_data["method"] == "send"
    assert request_data["params"]["message"] == "hello tcp"
    assert request_data["params"]["recipient"] == [SENDER]

    assert res.provider_message_id == f"{SENDER}:98765:{ACCOUNT}"
    assert res.provider_thread_id == SENDER