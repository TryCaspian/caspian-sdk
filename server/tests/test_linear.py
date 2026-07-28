import hashlib
import hmac
import json
import time

import httpx
import pytest
from comm_gateway.providers.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from comm_gateway.providers.fakes.fake_linear import FakeLinearProvider
from comm_gateway.providers.linear import LinearProvider, parse_linear_comment

SECRET = "sec_test_linear_123"


def compute_signature(payload: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_linear_client_id_property():
    provider = LinearProvider(client_id="app_12345")
    assert provider.client_id == "app_12345"


def test_linear_route_key():
    payload = json.dumps({"organizationId": "org_abc123", "action": "create"}).encode()
    assert LinearProvider.route_key(payload) == "org_abc123"
    assert LinearProvider.route_key(b"not json") is None
    assert LinearProvider.route_key(b"{}") is None
    assert LinearProvider.route_key(b"[1, 2, 3]") is None

    # Test nested organizationId fallback
    nested = json.dumps({"data": {"organizationId": "org_nested_789"}}).encode()
    assert LinearProvider.route_key(nested) == "org_nested_789"


def test_linear_signature_verification_success():
    provider = LinearProvider(webhook_secret=SECRET)
    payload = b'{"type": "Comment", "action": "create"}'
    sig = compute_signature(payload)
    headers = {"Linear-Signature": sig}

    # Should not raise
    provider._verify_signature(payload, headers)


def test_linear_signature_verification_failure():
    provider = LinearProvider(webhook_secret=SECRET)
    payload = b'{"type": "Comment", "action": "create"}'
    headers = {"Linear-Signature": "invalid_signature_hex"}

    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        provider._verify_signature(payload, headers)


def test_linear_signature_missing_header():
    provider = LinearProvider(webhook_secret=SECRET)
    payload = b'{"type": "Comment", "action": "create"}'

    with pytest.raises(WebhookVerificationError, match="signature header missing"):
        provider._verify_signature(payload, headers={})


def test_linear_parse_webhook_timestamp_validation():
    provider = LinearProvider(webhook_secret=SECRET)
    now_ms = int(time.time() * 1000)

    data_recent = {
        "action": "create",
        "type": "Comment",
        "organizationId": "org_123",
        "webhookTimestamp": now_ms,
        "data": {"id": "c1", "body": "test", "issue": {"identifier": "ENG-1"}},
    }
    payload_recent = json.dumps(data_recent).encode()
    sig_recent = compute_signature(payload_recent)
    assert len(provider.parse_webhook(payload_recent, {"Linear-Signature": sig_recent})) == 1

    data_old = {
        "action": "create",
        "type": "Comment",
        "organizationId": "org_123",
        "webhookTimestamp": now_ms - (600 * 1000),  # 10 minutes ago
        "data": {"id": "c1", "body": "test", "issue": {"identifier": "ENG-1"}},
    }
    payload_old = json.dumps(data_old).encode()
    sig_old = compute_signature(payload_old)
    with pytest.raises(WebhookVerificationError, match="timestamp too old"):
        provider.parse_webhook(payload_old, {"Linear-Signature": sig_old})


def test_linear_parse_webhook_comment_created():
    provider = LinearProvider(webhook_secret=SECRET)
    data = {
        "action": "create",
        "type": "Comment",
        "organizationId": "org_linear_456",
        "actor": {"id": "usr_789", "name": "Jane Doe", "email": "jane@example.com", "type": "user"},
        "data": {
            "id": "comment_101",
            "body": "Fixing issue ENG-42 via PR",
            "issue": {"id": "issue_uuid_202", "identifier": "ENG-42"},
            "user": {"id": "usr_789", "name": "Jane Doe", "email": "jane@example.com"},
        },
    }
    payload = json.dumps(data).encode()
    sig = compute_signature(payload)
    headers = {"Linear-Signature": sig, "linear-delivery": "deliv_999"}

    messages = provider.parse_webhook(payload, headers)
    assert len(messages) == 1

    msg = messages[0]
    assert msg.provider_inbox_id == "org_linear_456"
    assert msg.provider_thread_id == "ENG-42"
    assert msg.provider_message_id == "ENG-42:comment_101"
    assert msg.external_event_id == "deliv_999"
    assert msg.sender_address == "jane@example.com"
    assert msg.sender_name == "Jane Doe"
    assert msg.text == "Fixing issue ENG-42 via PR"
    assert msg.chat_type == "issue"


def test_linear_parse_webhook_bot_ignored():
    data = {
        "action": "create",
        "type": "Comment",
        "organizationId": "org_linear_456",
        "actor": {"id": "app_bot", "name": "Caspian Bot", "type": "app"},
        "data": {
            "id": "comment_102",
            "body": "Automated response",
            "issue": {"id": "issue_uuid_202", "identifier": "ENG-42"},
        },
    }
    messages = parse_linear_comment(data)
    assert messages == []


def test_linear_parse_webhook_malformed_inputs():
    assert parse_linear_comment("not a dict") == []  # type: ignore
    assert parse_linear_comment({"type": "Comment", "action": "create", "data": "not a dict"}) == []


def test_linear_parse_webhook_non_comment_ignored():
    data = {
        "action": "create",
        "type": "Issue",
        "organizationId": "org_linear_456",
        "data": {"id": "issue_123", "title": "New issue"},
    }
    messages = parse_linear_comment(data)
    assert messages == []


def test_linear_provision():
    provider = LinearProvider()
    result = provider.provision(
        ProvisionRequest(
            connection_id="conn_1",
            customer_id="cust_1",
            agent_id="agent_1",
            credentials={"address": "linear:acme", "organization_id": "org_777"},
        )
    )
    assert result.address == "linear:acme"
    assert result.provider_resource_id == "org_777"


def test_linear_send_and_reply_success():
    requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": {"id": f"created_comment_{len(requests)}"},
                    }
                }
            },
        )

    provider = LinearProvider(base_url="https://api.linear.app")
    provider._client = httpx.Client(
        base_url="https://api.linear.app",
        transport=httpx.MockTransport(mock_handler),
    )

    credentials = {"api_key": "lin_api_key_test_123"}

    # Test send()
    sent = provider.send(
        "org_456",
        OutboundMessage(to=["ENG-42"], text="Resolving issue via PR"),
        credentials,
    )

    # Test reply()
    replied = provider.reply(
        "org_456",
        "ENG-42:comment_101",
        OutboundMessage(text="Follow up reply"),
        credentials,
    )

    assert sent.provider_message_id == "ENG-42:created_comment_1"
    assert sent.provider_thread_id == "ENG-42"

    assert replied.provider_message_id == "ENG-42:created_comment_2"
    assert replied.provider_thread_id == "ENG-42"

    assert len(requests) == 2
    assert all(r.url.path == "/graphql" for r in requests)
    assert all(r.headers["authorization"] == "lin_api_key_test_123" for r in requests)

    # Validate send GraphQL request query and variables
    req1_json = json.loads(requests[0].content)
    assert "mutation CreateComment" in req1_json["query"]
    assert req1_json["variables"] == {"issueId": "ENG-42", "body": "Resolving issue via PR"}

    # Validate reply GraphQL request query and variables
    req2_json = json.loads(requests[1].content)
    assert "mutation CreateComment" in req2_json["query"]
    assert req2_json["variables"] == {"issueId": "ENG-42", "body": "Follow up reply"}


def test_linear_send_requires_destination():
    provider = LinearProvider()
    with pytest.raises(ValueError, match="requires target issue"):
        provider.send("org_123", OutboundMessage(text="hello"), {"api_key": "key"})


def test_linear_send_requires_api_key():
    provider = LinearProvider()
    with pytest.raises(ValueError, match="API key / access token is required"):
        provider.send("org_123", OutboundMessage(to=["ENG-42"], text="hello"), {})


def test_linear_send_graphql_error():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": [{"message": "Entity not found"}]},
        )

    provider = LinearProvider()
    provider._client = httpx.Client(
        base_url="https://api.linear.app",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(RuntimeError, match="Linear API error: Entity not found"):
        provider.send(
            "org_123",
            OutboundMessage(to=["INVALID-999"], text="test"),
            {"api_key": "key"},
        )


def test_linear_send_http_error():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    provider = LinearProvider()
    provider._client = httpx.Client(
        base_url="https://api.linear.app",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(RuntimeError, match="Linear HTTP request failed"):
        provider.send(
            "org_123",
            OutboundMessage(to=["ENG-42"], text="test"),
            {"api_key": "key"},
        )


def test_fake_linear_provider_round_trip():
    provider = FakeLinearProvider()
    payload = json.dumps(provider.webhook_payload()).encode()
    messages = provider.parse_webhook(
        payload,
        {"linear-delivery": "fake-delivery"},
    )
    assert len(messages) == 1
    assert messages[0].external_event_id == "fake-delivery"
    assert provider.route_key(payload) == provider.organization_id

    # Test provision synchronizes organization_id
    prov_res = provider.provision(
        ProvisionRequest(
            connection_id="conn_1",
            customer_id="cust_1",
            agent_id="agent_1",
            credentials={"organization_id": "org_sync_999"},
        )
    )
    assert prov_res.provider_resource_id == "org_sync_999"
    assert provider.organization_id == "org_sync_999"

    sent = provider.send("org_123", OutboundMessage(to=["ENG-42"], text="Hello"), {})
    replied = provider.reply("org_123", "ENG-42:1001", OutboundMessage(text="Reply"), {})
    assert sent.provider_thread_id == "ENG-42"
    assert replied.provider_thread_id == "ENG-42"
