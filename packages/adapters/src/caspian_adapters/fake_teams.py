"""In-memory Microsoft Teams provider for local development and tests.

Generates its own RSA keypair and signs real JWTs with it, so parse_webhook
exercises the exact same signature-verification path (verify_activity_jwt) as
the live adapter - just against a JWKS it hands itself instead of Bot
Framework's, so nothing here ever touches the network.
"""

import base64
import json
import secrets
import time
from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from .teams import ALLOWED_ISSUERS, TeamsProvider, parse_activity, verify_activity_jwt


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FakeTeamsProvider:
    name = "fake-teams"
    channel = "teams"
    capabilities = TeamsProvider.capabilities
    connect_credentials = ()
    # zero-config for tests, but honors app_id when supplied so the per-bot
    # audience check is exercised exactly like the live adapter
    optional_connect_credentials = ("app_id",)

    def __init__(self) -> None:
        self.app_id = f"fake-teams-app-{secrets.token_hex(4)}"
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._kid = "fake-kid-1"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def _app_id(self, credentials: Mapping[str, str] | None) -> str:
        return (credentials or {}).get("app_id") or self.app_id

    def jwks(self) -> dict:
        """The JWKS matching this fake's signing key - hand this to
        verify_activity_jwt (or a TeamsProvider(jwks_fetcher=...)) in tests."""
        public_numbers = self._private_key.public_key().public_numbers()
        n = public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
        e = public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
        return {"keys": [{"kty": "RSA", "kid": self._kid, "n": _b64url(n), "e": _b64url(e)}]}

    def sign_activity_jwt(
        self, app_id: str, *, issuer: str | None = None, expires_in: int = 3600
    ) -> str:
        """A real, RS256-signed JWT claiming the given app id as audience -
        exactly the shape Bot Connector attaches as the inbound Authorization
        header, just signed with this fake's own (never-shared) key."""
        header = {"alg": "RS256", "typ": "JWT", "kid": self._kid}
        now = time.time()
        claims = {
            "aud": app_id,
            "iss": issuer if issuer is not None else next(iter(ALLOWED_ISSUERS)),
            "iat": int(now),
            "exp": int(now) + expires_in,
        }
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + _b64url(json.dumps(claims, separators=(",", ":")).encode())
        )
        signature = self._private_key.sign(
            signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return signing_input + "." + _b64url(signature)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        app_id = self._app_id(request.credentials)
        return ProvisionResult(address=f"fake-teams:{app_id}", provider_resource_id=app_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        thread = message.to[0]
        self._seq += 1
        self.sent.append({"thread": thread, "text": message.text})
        return SendResult(provider_message_id=f"{thread}:act{self._seq}", provider_thread_id=thread)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        thread, target_activity_id = provider_message_id.rsplit(":", 1)
        self._seq += 1
        self.replies.append(
            {"thread": thread, "in_reply_to": target_activity_id, "text": message.text}
        )
        return SendResult(provider_message_id=f"{thread}:act{self._seq}", provider_thread_id=thread)

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        app_id = self._app_id(credentials)
        auth = None
        for k, v in headers.items():
            if k.lower() == "authorization":
                auth = v
                break
        if not auth or not auth.lower().startswith("bearer "):
            raise WebhookVerificationError("missing bearer token")
        token = auth.split(" ", 1)[1]
        verify_activity_jwt(token, app_id, self.jwks())
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_activity(data, app_id)

    def activity_payload(
        self,
        *,
        conversation_id: str = "19:abcdef@thread.tacv2",
        service_url: str = "https://smba.trafficmanager.net/amer/",
        text: str = "Hi there",
        sender_id: str = "29:user-object-id",
        sender_name: str = "Customer",
        activity_id: str | None = None,
        conversation_type: str = "personal",
    ) -> dict:
        self._seq += 1
        return {
            "type": "message",
            "id": activity_id or f"activity-{self._seq}",
            "timestamp": "2026-07-25T00:00:00.000Z",
            "serviceUrl": service_url,
            "conversation": {"id": conversation_id, "conversationType": conversation_type},
            "from": {"id": sender_id, "name": sender_name},
            "text": text,
        }