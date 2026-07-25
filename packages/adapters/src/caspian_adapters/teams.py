"""Microsoft Teams adapter (Bot Framework / Azure Bot Service, one bot per connection).

Teams has no bring-your-own-webhook flow the way Telegram does; instead a
developer registers an Azure Bot (app id + password) and points it at the
gateway's messaging endpoint. Bot Framework then POSTs Activity objects to
that endpoint for every inbound message, and outbound replies go through the
Bot Connector REST API at the activity's own `serviceUrl` (Bot Framework is
multi-tenant across Azure clouds, so `serviceUrl` — not a fixed base URL — is
the address to reply to).

- provision is a no-op confirmation (App Studio, not us, registers the bot)
- inbound is a webhook verified via the JWT the Bot Connector signs, checked
  against the Bot Framework JWKS (audience = our app id, issuer = Bot
  Framework's own issuer)
- provider_thread_id packs {conversation.id, serviceUrl} (base64 JSON) since
  serviceUrl varies per tenant/cloud and must travel with the thread for a
  later reply/send; provider_message_id is "{thread}:{activity_id}" so a
  reply routes without an extra lookup (composite ids never leave this
  package)
"""

import base64
import json
import time
from collections.abc import Mapping

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    split_composite_id,
)

LOGIN_BASE_URL = "https://login.microsoftonline.com"
TOKEN_SCOPE = "https://api.botframework.com/.default"
# Bot Connector's own token issuer (public multi-tenant cloud); government/
# sovereign clouds use a different issuer and are out of scope for this adapter.
ALLOWED_ISSUERS = frozenset({"https://api.botframework.com"})
# How stale a cached JWKS can be before we refetch even without a kid miss.
JWKS_MAX_AGE = 24 * 60 * 60


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def _jwk_to_public_key(jwk: dict) -> rsa.RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise WebhookVerificationError(f"unsupported JWK key type: {jwk.get('kty')!r}")
    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def _pack_thread(conversation_id: str, service_url: str) -> str:
    raw = json.dumps({"c": conversation_id, "s": service_url}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _unpack_thread(token: str) -> tuple[str, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode()))
        return data["c"], data["s"]
    except Exception as exc:
        raise ValueError(f"malformed Teams thread token: {token!r}") from exc


def verify_activity_jwt(
    token: str, app_id: str, jwks: Mapping[str, list[dict]], *, now: float | None = None
) -> dict:
    """Verify a Bot Connector-signed JWT and return its claims.

    `jwks` is the JSON Web Key Set to check against (the caller resolves this,
    live or offline, so verification itself never touches the network).
    Raises WebhookVerificationError on any failure: bad shape, unknown key,
    bad signature, wrong audience/issuer, or expiry.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise WebhookVerificationError("malformed JWT (expected header.payload.signature)")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except Exception as exc:
        raise WebhookVerificationError("malformed JWT segment") from exc
    if header.get("alg") != "RS256":
        raise WebhookVerificationError(f"unsupported JWT alg: {header.get('alg')!r}")
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if key is None:
        raise WebhookVerificationError("no matching JWKS key for token kid")
    public_key = _jwk_to_public_key(key)
    signing_input = f"{header_b64}.{payload_b64}".encode()
    try:
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise WebhookVerificationError("JWT signature mismatch") from exc
    now = now if now is not None else time.time()
    if claims.get("aud") != app_id:
        raise WebhookVerificationError("JWT audience does not match this bot's app id")
    if claims.get("iss") not in ALLOWED_ISSUERS:
        raise WebhookVerificationError(f"unexpected JWT issuer: {claims.get('iss')!r}")
    if "exp" in claims and now >= claims["exp"]:
        raise WebhookVerificationError("JWT has expired")
    if "nbf" in claims and now < claims["nbf"]:
        raise WebhookVerificationError("JWT not yet valid")
    return claims


def parse_activity(data: dict, app_id: str) -> list[InboundMessage]:
    """Normalize a Bot Framework Activity into our schema (text messages only;
    conversationUpdate / typing / other activity types are not messages)."""
    if data.get("type") != "message":
        return []
    text = data.get("text")
    if not text:
        return []
    conversation = data.get("conversation") or {}
    conversation_id = conversation.get("id")
    service_url = data.get("serviceUrl")
    if not conversation_id or not service_url:
        return []
    sender = data.get("from") or {}
    thread = _pack_thread(conversation_id, service_url)
    return [
        InboundMessage(
            external_event_id=data.get("id") or f"{thread}:{data.get('timestamp', '')}",
            provider_inbox_id=app_id,
            provider_message_id=f"{thread}:{data.get('id', '')}",
            provider_thread_id=thread,
            sender_address=sender.get("id"),
            sender_name=sender.get("name"),
            text=text,
            chat_type="group" if conversation.get("conversationType") == "channel"
            else (conversation.get("conversationType") or "personal"),
        )
    ]


class TeamsProvider:
    name = "teams"
    channel = "teams"
    # One Azure Bot per developer (App Studio has no API to create one for
    # them), so app id + password arrive at connect time - same bring-your-own
    # pattern as Telegram's bot_token.
    connect_credentials = ("app_id", "app_password")
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        login_base_url: str = LOGIN_BASE_URL,
        jwks_fetcher=None,
    ) -> None:
        self._login_base_url = login_base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)
        # Overridable so tests (and sovereign clouds, eventually) never hit the
        # real network; defaults to the live two-step OpenID discovery + JWKS
        # fetch, cached in-memory since Bot Framework's signing keys rotate
        # rarely.
        self._jwks_fetcher = jwks_fetcher or self._fetch_jwks_live
        self._jwks_cache: dict | None = None
        self._jwks_cached_at = 0.0
        self._token_cache: dict[str, tuple[str, float]] = {}

    def _fetch_jwks_live(self) -> dict:
        config_url = (
            f"{self._login_base_url}/botframework.com"
            "/v2.0/.well-known/openid-configuration"
        )
        config = self._client.get(config_url).raise_for_status().json()
        return self._client.get(config["jwks_uri"]).raise_for_status().json()

    def _jwks(self, *, force: bool = False) -> dict:
        stale = time.time() - self._jwks_cached_at > JWKS_MAX_AGE
        if force or self._jwks_cache is None or stale:
            self._jwks_cache = self._jwks_fetcher()
            self._jwks_cached_at = time.time()
        return self._jwks_cache

    def _token(self, app_id: str, app_password: str) -> str:
        cached = self._token_cache.get(app_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        r = self._client.post(
            f"{self._login_base_url}/botframework.com/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_password,
                "scope": TOKEN_SCOPE,
            },
        )
        r.raise_for_status()
        data = r.json()
        expires_at = time.time() + int(data.get("expires_in", 3600))
        self._token_cache[app_id] = (data["access_token"], expires_at)
        return data["access_token"]

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        app_id = (request.credentials or {}).get("app_id", "")
        return ProvisionResult(address=f"teams:{app_id}", provider_resource_id=app_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        creds = credentials or {}
        thread = message.to[0]
        conversation_id, service_url = _unpack_thread(thread)
        token = self._token(creds["app_id"], creds["app_password"])
        r = self._client.post(
            f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities",
            json={"type": "message", "text": message.text or ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        activity_id = r.json().get("id", "")
        return SendResult(provider_message_id=f"{thread}:{activity_id}", provider_thread_id=thread)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        creds = credentials or {}
        thread, target_activity_id = split_composite_id(provider_message_id)
        conversation_id, service_url = _unpack_thread(thread)
        token = self._token(creds["app_id"], creds["app_password"])
        r = self._client.post(
            f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}"
            f"/activities/{target_activity_id}",
            json={"type": "message", "text": message.text or ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        activity_id = r.json().get("id", target_activity_id)
        return SendResult(provider_message_id=f"{thread}:{activity_id}", provider_thread_id=thread)

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        creds = credentials or {}
        app_id = creds.get("app_id")
        if not app_id:
            raise WebhookVerificationError("Teams webhooks require a connection scope")
        auth = None
        for k, v in headers.items():
            if k.lower() == "authorization":
                auth = v
                break
        if not auth or not auth.lower().startswith("bearer "):
            raise WebhookVerificationError("missing bearer token")
        token = auth.split(" ", 1)[1]
        try:
            verify_activity_jwt(token, app_id, self._jwks())
        except WebhookVerificationError as exc:
            # Only worth a refetch if our cached JWKS simply doesn't have this
            # key yet (a recent rotation) - any other failure won't be fixed
            # by fetching the same keys again.
            if "no matching JWKS key" not in str(exc):
                raise
            verify_activity_jwt(token, app_id, self._jwks(force=True))
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_activity(data, app_id)