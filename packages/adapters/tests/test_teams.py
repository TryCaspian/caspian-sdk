"""Microsoft Teams adapter: Activity normalization and Bot Framework JWT checks."""

import base64
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.teams import (
    FakeTeamsProvider,
    TeamsProvider,
    _message_id,
    parse_activity,
    teams_message_activity,
)
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.hashes import SHA256

APP_ID = "00000000-0000-0000-0000-000000000001"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt(private_key, *, aud=APP_ID, kid="kid1", exp_delta=300) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    claims = {
        "iss": "https://api.botframework.com",
        "aud": aud,
        "nbf": now - 10,
        "exp": now + exp_delta,
    }
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode())
        for part in (header, claims)
    ).encode()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), SHA256())
    return f"{signing_input.decode()}.{_b64url(signature)}"


def _jwk(public_key, kid="kid1"):
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


class StaticVerifier:
    def __init__(self, token: str):
        self.token = token
        self.audience = ""

    def verify(self, token: str, audience: str, channel_id: str | None = None) -> dict:
        self.audience = audience
        if token != self.token:
            raise WebhookVerificationError("Bot Framework JWT signature mismatch")
        return {"aud": audience}


def test_parse_activity_normalizes_message():
    [inbound] = parse_activity(teams_message_activity(text="hello teams"), APP_ID)
    assert inbound.provider_inbox_id == APP_ID
    assert inbound.provider_message_id == _message_id("19:abc@thread.tacv2", "1690000000000")
    assert inbound.provider_thread_id == "19:abc@thread.tacv2"
    assert inbound.sender_address == "29:user"
    assert inbound.sender_name == "Alice Ng"
    assert inbound.recipients == [{"id": "28:bot", "name": "Caspian Bot"}]
    assert inbound.text == "hello teams"
    assert inbound.chat_type == "group"


def test_parse_activity_extracts_attachments_and_skips_non_messages():
    data = teams_message_activity(text="see file")
    data["attachments"] = [
        {
            "contentType": "application/pdf",
            "contentUrl": "https://example.invalid/report.pdf",
            "name": "report.pdf",
            "id": "att1",
        }
    ]
    [inbound] = parse_activity(data, APP_ID)
    assert inbound.attachments[0].url == "https://example.invalid/report.pdf"
    assert inbound.attachments[0].mime_type == "application/pdf"
    assert inbound.attachments[0].filename == "report.pdf"
    assert parse_activity({"type": "conversationUpdate"}, APP_ID) == []


def test_parse_webhook_verifies_bearer_token_accept_and_reject():
    verifier = StaticVerifier("good-token")
    provider = TeamsProvider(verifier=verifier)
    payload = json.dumps(teams_message_activity()).encode()
    [inbound] = provider.parse_webhook(
        payload, {"Authorization": "Bearer good-token"}, credentials={"app_id": APP_ID}
    )
    assert inbound.provider_inbox_id == APP_ID
    assert verifier.audience == APP_ID

    with pytest.raises(WebhookVerificationError, match="bearer token"):
        provider.parse_webhook(payload, {}, credentials={"app_id": APP_ID})
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        provider.parse_webhook(
            payload, {"Authorization": "Bearer bad-token"}, credentials={"app_id": APP_ID}
        )


def test_bot_framework_jwt_verifier_accepts_and_rejects_jwks_tokens():
    from caspian_adapters.teams import BotFrameworkJwtVerifier

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = BotFrameworkJwtVerifier()
    verifier._jwks = {"keys": [_jwk(private_key.public_key())]}

    claims = verifier.verify(_jwt(private_key), APP_ID)
    assert claims["aud"] == APP_ID

    with pytest.raises(WebhookVerificationError, match="audience"):
        verifier.verify(_jwt(private_key, aud="wrong"), APP_ID)

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(WebhookVerificationError, match="signature"):
        verifier.verify(_jwt(other_key), APP_ID)


def test_teams_provider_caches_access_tokens_per_credential_set():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == httpx.URL("https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token")
        assert request.method == "POST"
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["client_credentials"]
        assert body["client_id"] == ["app-1"]
        assert body["client_secret"] == ["secret-1"]
        assert body["scope"] == ["https://api.botframework.com/.default"]
        return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})

    provider = TeamsProvider()
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))

    assert provider._access_token({"app_id": "app-1", "app_password": "secret-1"}) == "token-1"
    assert provider._access_token({"app_id": "app-1", "app_password": "secret-1"}) == "token-1"
    assert len(requests) == 1


def test_teams_provider_posts_to_preserved_service_url_and_parses_response():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "token-2", "expires_in": 3600})
        assert request.url == httpx.URL(
            "https://example.invalid/service/v3/conversations/19:team@thread.tacv2/activities"
        )
        assert request.headers["authorization"] == "Bearer token-2"
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "type": "message",
            "text": "pong",
            "replyToId": "reply-1",
        }
        return httpx.Response(200, json={"id": "activity-123"})

    provider = TeamsProvider()
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    provider._conversation_service_urls["19:team@thread.tacv2"] = "https://example.invalid/service"

    result = provider.reply(
        APP_ID,
        _message_id("19:team@thread.tacv2", "reply-1"),
        OutboundMessage(text="pong"),
        credentials={"app_id": "app-2", "app_password": "secret-2"},
    )

    assert result.provider_thread_id == "19:team@thread.tacv2"
    assert result.provider_message_id == _message_id("19:team@thread.tacv2", "activity-123")
    assert len(requests) == 2


def test_teams_provider_requires_recipient_for_send():
    provider = TeamsProvider()

    with pytest.raises(ValueError, match="recipient"):
        provider.send(APP_ID, OutboundMessage(text="hello"))


def test_fake_teams_consumes_realistic_activity_shape_and_routes_replies():
    provider = FakeTeamsProvider()
    payload = provider.webhook_payload(conversation_id="19:team@thread.tacv2", text="ping")
    [inbound] = provider.parse_webhook(
        json.dumps(payload).encode(), {}, credentials={"app_id": APP_ID}
    )
    assert inbound.text == "ping"
    assert inbound.provider_inbox_id == APP_ID

    provider.reply(APP_ID, inbound.provider_message_id, OutboundMessage(text="pong"))
    assert provider.replies == [
        {"conversation": "19:team@thread.tacv2", "reply_to": "msg-1", "text": "pong"}
    ]


def test_capabilities_are_honest():
    from caspian_adapters.base import Capability
    from caspian_adapters.teams import TeamsProvider

    assert Capability.INITIATE not in TeamsProvider.capabilities
    assert Capability.BACKFILL not in TeamsProvider.capabilities
