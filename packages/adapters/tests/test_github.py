"""GitHub adapter: issue-comment normalization, verification, routing, and replies."""

import hashlib
import hmac
import json

import httpx
import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.fake_github import FakeGitHubProvider
from caspian_adapters.github import GitHubProvider, parse_issue_comment
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SECRET = "github-webhook-secret"
SLUG = "caspian-test"


def _event(
    *,
    text=f"@{SLUG} please help",
    action="created",
    user_type="User",
    pull_request=False,
):
    issue = {"number": 42}
    if pull_request:
        issue["pull_request"] = {"url": "https://api.github.com/repos/acme/widget/pulls/42"}
    return {
        "action": action,
        "installation": {"id": 12345},
        "repository": {"full_name": "acme/widget"},
        "issue": issue,
        "comment": {
            "id": 987,
            "body": text,
            "user": {"login": "octocat", "type": user_type},
        },
    }


def _headers(payload: bytes, secret=SECRET, event="issue_comment"):
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
    }


def _provider(**kwargs):
    return GitHubProvider(app_id="1", app_slug=SLUG, webhook_secret=SECRET, **kwargs)


def test_parse_issue_comment_normalizes_issue():
    message = parse_issue_comment(_event(), app_slug=SLUG, delivery_id="delivery-1")[0]
    assert message.external_event_id == "delivery-1"
    assert message.provider_inbox_id == "12345"
    assert message.provider_thread_id == "acme/widget#42"
    assert message.provider_message_id == "acme/widget#42:987"
    assert message.sender_address == "octocat"
    assert message.chat_type == "issue"


def test_parse_issue_comment_marks_pull_request():
    message = parse_issue_comment(_event(pull_request=True), app_slug=SLUG)[0]
    assert message.chat_type == "pull_request"


def test_default_mode_requires_mention_and_ignores_bots():
    assert parse_issue_comment(_event(text="not for the app"), app_slug=SLUG) == []
    assert parse_issue_comment(_event(user_type="Bot"), app_slug=SLUG) == []
    assert parse_issue_comment(_event(action="deleted"), app_slug=SLUG) == []
    assert parse_issue_comment(_event(text="visible"), receive_mode="all") != []


def test_parse_webhook_accepts_valid_signature():
    provider = _provider()
    payload = json.dumps(_event()).encode()
    message = provider.parse_webhook(payload, _headers(payload))[0]
    assert message.text == f"@{SLUG} please help"


def test_parse_webhook_rejects_bad_or_missing_signature():
    provider = _provider()
    payload = json.dumps(_event()).encode()
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        provider.parse_webhook(payload, _headers(payload, secret="wrong"))
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        provider.parse_webhook(payload, {"X-GitHub-Event": "issue_comment"})


def test_parse_webhook_requires_configured_secret():
    provider = GitHubProvider(app_slug=SLUG)
    payload = json.dumps(_event()).encode()
    with pytest.raises(WebhookVerificationError, match="not configured"):
        provider.parse_webhook(payload, _headers(payload))


def test_ping_and_unhandled_events_return_no_messages():
    provider = _provider()
    payload = json.dumps({"zen": "Keep it logically awesome."}).encode()
    assert provider.parse_webhook(payload, _headers(payload, event="ping")) == []
    assert provider.parse_webhook(payload, _headers(payload, event="push")) == []


def test_route_key_uses_installation_id():
    assert GitHubProvider.route_key(json.dumps(_event()).encode()) == "12345"
    assert GitHubProvider.route_key(b"not-json") is None
    assert GitHubProvider.route_key(b"{}") is None


def test_installation_exchange_mints_token_and_returns_routing_metadata():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.headers["authorization"].startswith("Bearer eyJ")
        if request.method == "GET":
            return httpx.Response(200, json={"account": {"login": "acme"}})
        return httpx.Response(
            201,
            json={"token": "ghs_installation", "expires_at": "2030-01-01T00:00:00Z"},
        )

    provider = GitHubProvider(
        app_id="123",
        app_slug=SLUG,
        private_key=pem,
        webhook_secret=SECRET,
    )
    provider._client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    result = provider.exchange_installation("999")

    assert result["provider_resource_id"] == "999"
    assert result["address"] == "github:acme"
    assert result["credentials"]["installation_token"] == "ghs_installation"
    assert [request.url.path for request in requests] == [
        "/app/installations/999",
        "/app/installations/999/access_tokens",
    ]


def test_send_and_reply_create_issue_comments():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json={"id": 1000 + len(requests)})

    provider = _provider()
    provider._client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    credentials = {"installation_token": "ghs_test"}
    sent = provider.send(
        "12345",
        OutboundMessage(text="new comment", to=("acme/widget#42",)),
        credentials,
    )
    replied = provider.reply(
        "12345",
        "acme/widget#42:987",
        OutboundMessage(text="reply"),
        credentials,
    )

    assert sent.provider_message_id == "acme/widget#42:1001"
    assert replied.provider_message_id == "acme/widget#42:1002"
    assert all(request.url.path == "/repos/acme/widget/issues/42/comments" for request in requests)
    assert all(request.headers["authorization"] == "Bearer ghs_test" for request in requests)
    assert requests[0].headers["x-github-api-version"] == "2022-11-28"
    assert json.loads(requests[1].content) == {"body": "reply"}


def test_send_rejects_invalid_destination():
    provider = _provider()
    with pytest.raises(ValueError, match="owner/repo"):
        provider.send(
            "12345",
            OutboundMessage(text="hello", to=("not-a-thread",)),
            {"installation_token": "ghs_test"},
        )


def test_fake_provider_round_trip_and_refresh():
    provider = FakeGitHubProvider(app_slug=SLUG)
    payload = json.dumps(provider.webhook_payload()).encode()
    messages = provider.parse_webhook(
        payload,
        {"X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "fake-delivery"},
    )
    assert messages[0].external_event_id == "fake-delivery"
    assert provider.route_key(payload) == provider.installation_id

    expired = {
        "installation_id": provider.installation_id,
        "installation_token": "old",
        "token_expires_at": 0,
    }
    assert provider.needs_refresh(expired)
    refreshed = provider.refresh_credentials(expired)
    assert refreshed["installation_token"].startswith("ghs_fake_refreshed_")
