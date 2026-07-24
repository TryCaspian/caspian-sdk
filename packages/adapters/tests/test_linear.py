"""Linear adapter: webhook normalization, signing, GraphQL outbound, fake."""

import hashlib
import hmac
import json
import time

import httpx
import pytest
from caspian_adapters import OutboundMessage, ProvisionRequest, Settings, build_providers
from caspian_adapters.base import Capability, WebhookVerificationError
from caspian_adapters.linear import (
    MAX_WEBHOOK_TIMESTAMP_SKEW_MS,
    FakeLinearProvider,
    LinearProvider,
    issue_id_from_provider_message_id,
    parse_linear_webhook,
)

SECRET = "linear-secret"


def _issue_payload():
    return {
        "action": "create",
        "type": "Issue",
        "organizationId": "org_123",
        "webhookTimestamp": 1752000000000,
        "data": {
            "id": "issue_123",
            "identifier": "ENG-42",
            "title": "Checkout fails",
            "description": "Card payments return a 500.",
            "creator": {
                "id": "user_1",
                "name": "Ada Lovelace",
                "email": "ada@example.com",
            },
        },
    }


def _comment_payload():
    return {
        "action": "create",
        "type": "Comment",
        "organizationId": "org_123",
        "webhookTimestamp": 1752000000001,
        "data": {
            "id": "comment_123",
            "body": "Can the agent take a look?",
            "issue": {"id": "issue_123", "identifier": "ENG-42", "title": "Checkout fails"},
            "user": {"id": "user_2", "name": "Grace Hopper"},
        },
    }


def _signed_headers(payload: bytes, secret=SECRET):
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return {"Linear-Signature": signature, "Linear-Delivery": "delivery_123"}


def _fresh_payload(payload: dict | None = None, *, offset_ms: int = 0) -> dict:
    fresh = dict(payload or _issue_payload())
    fresh["webhookTimestamp"] = int(time.time() * 1000) + offset_ms
    return fresh


def _provider(handler=None) -> LinearProvider:
    provider = LinearProvider(api_key="lin_api_fake", webhook_secret=SECRET)
    provider._base_url = "https://linear.test/graphql"
    provider._client = httpx.Client(
        transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200, json={})))
    )
    return provider


def test_credentials_are_optional_for_shared_deployments():
    assert LinearProvider.connect_credentials == ()
    assert "api_key" in LinearProvider.optional_connect_credentials
    assert "webhook_secret" in LinearProvider.optional_connect_credentials


def test_capabilities_are_honest():
    assert LinearProvider.capabilities == frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND}
    )
    assert Capability.INITIATE not in LinearProvider.capabilities
    assert Capability.BACKFILL not in LinearProvider.capabilities


def test_parse_issue_webhook_normalizes_message():
    [inbound] = parse_linear_webhook(_issue_payload(), "delivery_123")
    assert inbound.external_event_id == "delivery_123"
    assert inbound.provider_inbox_id == "org_123"
    assert inbound.provider_message_id == "issue_123"
    assert inbound.provider_thread_id == "issue_123"
    assert inbound.sender_address == "ada@example.com"
    assert inbound.sender_name == "Ada Lovelace"
    assert inbound.subject == "ENG-42: Checkout fails"
    assert inbound.text == "Card payments return a 500."
    assert inbound.chat_type == "linear_issue"


def test_parse_comment_webhook_normalizes_message():
    [inbound] = parse_linear_webhook(_comment_payload(), "delivery_456")
    assert inbound.external_event_id == "delivery_456"
    assert inbound.provider_message_id == "issue_123:comment_123"
    assert inbound.provider_thread_id == "issue_123"
    assert inbound.sender_address == "user_2"
    assert inbound.sender_name == "Grace Hopper"
    assert inbound.subject == "ENG-42: Checkout fails"
    assert inbound.text == "Can the agent take a look?"
    assert inbound.chat_type == "linear_comment"


def test_parse_webhook_ignores_non_create_actions():
    payload = _issue_payload()
    payload["action"] = "update"
    assert parse_linear_webhook(payload) == []


def test_parse_webhook_verifies_signature_and_normalizes():
    provider = _provider()
    payload = json.dumps(_fresh_payload(), separators=(",", ":")).encode()
    inbound = provider.parse_webhook(payload, _signed_headers(payload))
    assert len(inbound) == 1
    assert inbound[0].external_event_id == "delivery_123"


def test_parse_webhook_rejects_bad_signature():
    provider = _provider()
    payload = json.dumps(_issue_payload()).encode()
    headers = _signed_headers(payload, secret="wrong-secret")
    with pytest.raises(WebhookVerificationError, match="signature"):
        provider.parse_webhook(payload, headers)


@pytest.mark.parametrize(
    "offset_ms",
    [
        -(MAX_WEBHOOK_TIMESTAMP_SKEW_MS + 1),
        MAX_WEBHOOK_TIMESTAMP_SKEW_MS + 1,
    ],
)
def test_parse_webhook_rejects_stale_or_future_timestamp(offset_ms):
    provider = _provider()
    payload = json.dumps(_fresh_payload(offset_ms=offset_ms)).encode()

    with pytest.raises(WebhookVerificationError, match="freshness"):
        provider.parse_webhook(payload, _signed_headers(payload))


def test_parse_webhook_requires_valid_timestamp_after_signature_verification():
    provider = _provider()
    body = _issue_payload()
    body.pop("webhookTimestamp")
    payload = json.dumps(body).encode()

    with pytest.raises(WebhookVerificationError, match="timestamp"):
        provider.parse_webhook(payload, _signed_headers(payload))


def test_parse_webhook_requires_secret():
    provider = LinearProvider()
    payload = json.dumps(_issue_payload()).encode()
    with pytest.raises(WebhookVerificationError, match="secret"):
        provider.parse_webhook(payload, {"Linear-Signature": "bad"})


def test_route_key_is_organization_id():
    payload = json.dumps(_issue_payload()).encode()
    assert LinearProvider.route_key(payload) == "org_123"
    assert LinearProvider.route_key(b"not json") is None


def test_provision_queries_linear_organization():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://linear.test/graphql"
        seen["auth"] = request.headers["authorization"]
        body = json.loads(request.content)
        assert "organization" in body["query"]
        return httpx.Response(
            200,
            json={"data": {"organization": {"id": "org_123", "name": "Acme", "urlKey": "acme"}}},
        )

    provider = _provider(handler)
    result = provider.provision(
        ProvisionRequest(
            connection_id="conn_1",
            customer_id="cust_1",
            agent_id="agent_1",
            credentials={"api_key": "lin_api_connection"},
        )
    )
    assert seen["auth"] == "lin_api_connection"
    assert result.address == "linear:acme"
    assert result.provider_resource_id == "org_123"


def test_send_creates_issue_with_team_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["variables"] = body["variables"]
        assert "issueCreate" in body["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": "issue_new", "identifier": "ENG-99"},
                    }
                }
            },
        )

    provider = _provider(handler)
    result = provider.send(
        "org_123",
        OutboundMessage(subject="Investigate checkout", text="Full details", to=("team_123",)),
    )
    assert seen["variables"]["input"] == {
        "teamId": "team_123",
        "title": "Investigate checkout",
        "description": "Full details",
    }
    assert result.provider_message_id == "issue_new"
    assert result.provider_thread_id == "issue_new"


def test_send_empty_message_uses_fallback_title():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["variables"] = body["variables"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": "issue_new", "identifier": "ENG-99"},
                    }
                }
            },
        )

    provider = _provider(handler)
    provider.send("org_123", OutboundMessage(to=("team_123",)))

    assert seen["variables"]["input"] == {
        "teamId": "team_123",
        "title": "Caspian message",
        "description": "",
    }


def test_reply_creates_comment_on_source_issue():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["variables"] = body["variables"]
        assert "commentCreate" in body["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": {"id": "comment_new", "issue": {"id": "issue_123"}},
                    }
                }
            },
        )

    provider = _provider(handler)
    result = provider.reply("org_123", "issue_123:comment_123", OutboundMessage(text="On it"))
    assert seen["variables"]["input"] == {"issueId": "issue_123", "body": "On it"}
    assert result.provider_message_id == "issue_123:comment_new"
    assert result.provider_thread_id == "issue_123"


def test_issue_id_from_provider_message_id_accepts_issue_and_comment_ids():
    assert issue_id_from_provider_message_id("issue_123") == "issue_123"
    assert issue_id_from_provider_message_id("issue_123:comment_123") == "issue_123"


def test_fake_linear_provider_consumes_realistic_fixtures():
    provider = FakeLinearProvider()
    issue_payload = json.dumps(provider.issue_payload()).encode()
    comment_payload = json.dumps(provider.comment_payload(issue_id="issue_1")).encode()
    [issue] = provider.parse_webhook(issue_payload, {"Linear-Delivery": "delivery_issue"})
    [comment] = provider.parse_webhook(comment_payload, {"Linear-Delivery": "delivery_comment"})
    assert issue.chat_type == "linear_issue"
    assert comment.chat_type == "linear_comment"
    assert comment.provider_thread_id == "issue_1"
    assert FakeLinearProvider.route_key(issue_payload) == provider.organization_id


def test_registry_builds_linear_and_fake_linear():
    providers = build_providers(
        Settings(
            providers="linear,fake-linear",
            linear_api_key="lin_api_fake",
            linear_webhook_secret=SECRET,
        )
    )
    assert providers["linear"].channel == "linear"
    assert providers["fake-linear"].channel == "linear"
