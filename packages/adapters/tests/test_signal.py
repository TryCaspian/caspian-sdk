"""Tests for the Signal adapter."""

import json
from unittest.mock import patch

import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.signal import SignalProvider, parse_envelope

REGISTERED_NUMBER = "+1234567890"
SENDER_NUMBER = "+9876543210"
DAEMON_URL = "http://localhost:8080"


def test_parse_envelope_private_message():
    """Test parsing a private message."""
    data = {
        "id": "msg-123",
        "source": SENDER_NUMBER,
        "sourceName": "Alice",
        "timestamp": 1234567890,
        "dataMessage": {"message": "Hello from Signal!"},
    }

    messages = parse_envelope(data, REGISTERED_NUMBER)
    assert len(messages) == 1

    msg = messages[0]
    assert msg.text == "Hello from Signal!"
    assert msg.provider_inbox_id == REGISTERED_NUMBER
    assert msg.provider_message_id == f"signal:{SENDER_NUMBER}:1234567890:msg-123"
    assert msg.sender_address == SENDER_NUMBER
    assert msg.sender_name == "Alice"
    assert msg.chat_type == "private"
    assert msg.edited is False


def test_parse_envelope_group_message():
    """Test parsing a group message."""
    data = {
        "id": "group-msg-456",
        "source": SENDER_NUMBER,
        "dataMessage": {
            "message": "Group message!",
            "groupInfo": {"groupId": "group-abc-123"},
        },
    }

    messages = parse_envelope(data, REGISTERED_NUMBER)
    assert len(messages) == 1

    msg = messages[0]
    assert msg.chat_type == "group"
    assert msg.provider_thread_id == "group:group-abc-123"


def test_parse_envelope_with_attachments():
    """Test parsing a message with attachments."""
    data = {
        "id": "msg-with-att",
        "source": SENDER_NUMBER,
        "dataMessage": {
            "message": "Check this",
            "attachments": [
                {
                    "id": "att-1",
                    "filename": "photo.jpg",
                    "contentType": "image/jpeg",
                    "size": 102400,
                    "url": "https://signal-attachments/att-1",
                }
            ],
        },
    }

    messages = parse_envelope(data, REGISTERED_NUMBER)
    assert len(messages) == 1

    msg = messages[0]
    assert msg.text == "Check this"
    assert len(msg.attachments) == 1

    att = msg.attachments[0]
    assert att.provider_file_id == "att-1"
    assert att.filename == "photo.jpg"
    assert att.mime_type == "image/jpeg"
    assert att.size_bytes == 102400
    assert att.url == "https://signal-attachments/att-1"


def test_parse_envelope_skips_non_messages():
    """Test that non-message events are skipped."""
    data = {"type": "url_verification", "challenge": "test-challenge"}
    messages = parse_envelope(data, REGISTERED_NUMBER)
    assert messages == []


def test_parse_envelope_handles_jsonrpc_format():
    """Test parsing JSON-RPC receive format."""
    data = {
        "method": "receive",
        "params": {
            "result": {
                "envelope": {
                    "source": SENDER_NUMBER,
                    "id": "msg-789",
                    "timestamp": 1234567890,
                    "dataMessage": {"message": "JSON-RPC test"},
                }
            }
        },
    }

    messages = parse_envelope(data, REGISTERED_NUMBER)
    assert len(messages) == 1
    assert messages[0].text == "JSON-RPC test"


def test_parse_envelope_handles_bulk_messages():
    """Test parsing multiple messages in one payload."""
    data = {
        "messages": [
            {"id": "msg-1", "source": "+111", "dataMessage": {"message": "First"}},
            {"id": "msg-2", "source": "+222", "dataMessage": {"message": "Second"}},
        ]
    }

    messages = parse_envelope(data, REGISTERED_NUMBER)
    assert len(messages) == 2
    assert messages[0].text == "First"
    assert messages[1].text == "Second"


def test_parse_webhook_token_verification():
    """Test webhook token verification."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
        api_token="secret-token",
    )

    payload = {
        "id": "webhook-123",
        "source": SENDER_NUMBER,
        "dataMessage": {"message": "Webhook test"},
    }

    messages = provider.parse_webhook(
        payload=json.dumps(payload).encode(),
        headers={"X-Signal-Token": "secret-token"},
    )

    assert len(messages) == 1
    assert messages[0].text == "Webhook test"


def test_parse_webhook_invalid_token():
    """Test webhook with invalid token."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
        api_token="expected-token",
    )

    with pytest.raises(WebhookVerificationError, match="Invalid webhook token"):
        provider.parse_webhook(
            payload=b"{}",
            headers={"X-Signal-Token": "wrong-token"},
        )


def test_parse_webhook_url_verification():
    """Test URL verification challenge."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    payload = {"type": "url_verification", "challenge": "test-challenge"}

    messages = provider.parse_webhook(
        payload=json.dumps(payload).encode(),
        headers={},
    )

    assert messages == []


def test_parse_webhook_invalid_json():
    """Test invalid JSON handling."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with pytest.raises(WebhookVerificationError, match="Invalid JSON payload"):
        provider.parse_webhook(payload=b"not json", headers={})


def test_parse_webhook_empty_array():
    """Test empty array payload."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with pytest.raises(WebhookVerificationError, match="Invalid JSON payload"):
        provider.parse_webhook(payload=b"[]", headers={})


def test_send_message():
    """Test sending a message."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value.json.return_value = {"result": {"id": "sent-123"}}

        provider.send(
            provider_inbox_id=REGISTERED_NUMBER,
            message=OutboundMessage(text="Hello!", to=(SENDER_NUMBER,)),
        )

        # Verify JSON-RPC call
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["method"] == "send"
        assert payload["params"]["recipient"] == [SENDER_NUMBER]
        assert payload["params"]["message"] == "Hello!"
        assert payload["params"]["account"] == REGISTERED_NUMBER


def test_send_message_no_recipient():
    """Test sending without a recipient."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with pytest.raises(ValueError, match="No recipient specified"):
        provider.send(
            provider_inbox_id=REGISTERED_NUMBER,
            message=OutboundMessage(text="Hello!"),
        )


def test_reply_message():
    """Test replying to a message."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    composite_id = f"signal:{SENDER_NUMBER}:1234567890:msg-789"

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value.json.return_value = {"result": {"id": "reply-456"}}

        provider.reply(
            provider_inbox_id=REGISTERED_NUMBER,
            provider_message_id=composite_id,
            message=OutboundMessage(text="Reply!"),
        )

        # Verify quote references original
        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["quote"]["id"] == "msg-789"
        assert payload["params"]["quote"]["timestamp"] == 1234567890
        assert payload["params"]["quote"]["author"] == SENDER_NUMBER
        assert payload["params"]["recipient"] == [SENDER_NUMBER]


def test_reply_handles_old_format():
    """Test replying with old composite ID format."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value.json.return_value = {"result": {"id": "reply-456"}}

        provider.reply(
            provider_inbox_id=REGISTERED_NUMBER,
            provider_message_id="signal:msg-789",
            message=OutboundMessage(text="Reply!"),
        )

        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["quote"]["id"] == "msg-789"
        assert "timestamp" not in payload["params"]["quote"]


def test_send_group_message():
    """Test sending a group message."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value.json.return_value = {"result": {"id": "group-123"}}

        provider.send(
            provider_inbox_id=REGISTERED_NUMBER,
            message=OutboundMessage(text="Group hello!", to=("group:abc-123",)),
        )

        payload = mock_post.call_args[1]["json"]
        assert "groupId" in payload["params"]
        assert payload["params"]["groupId"] == "abc-123"
        assert "recipient" not in payload["params"]


def test_capabilities_honest():
    """Test that capabilities are honest."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    assert "initiate" not in provider.capabilities
    assert "backfill" not in provider.capabilities
    assert "receive" in provider.capabilities
    assert "reply" in provider.capabilities
    assert "send" in provider.capabilities
    assert "attachments" in provider.capabilities
    assert "group_visibility" in provider.capabilities


def test_provision():
    """Test provision returns configured number."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    result = provider.provision(None)
    assert result.address == REGISTERED_NUMBER
    assert result.provider_resource_id == REGISTERED_NUMBER
    assert result.provider_pod_id is None


def test_provision_requires_config():
    """Test provision requires both URL and number."""
    with pytest.raises(ValueError, match="required"):
        SignalProvider(daemon_url="", registered_number=REGISTERED_NUMBER)

    with pytest.raises(ValueError, match="required"):
        SignalProvider(daemon_url=DAEMON_URL, registered_number="")


def test_close_releases_client():
    """Test close releases HTTP client."""
    provider = SignalProvider(
        daemon_url=DAEMON_URL,
        registered_number=REGISTERED_NUMBER,
    )

    with patch("httpx.Client"):
        provider._http_client()
        assert provider._http is not None

        provider.close()
        assert provider._http is None