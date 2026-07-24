"""Microsoft Teams adapter via Azure Bot Service / Bot Framework.

Inbound Teams messages arrive as Bot Framework Activity objects at the bot's
messaging endpoint. Azure Bot Service signs them with a Bearer JWT; this module
validates that token against the Bot Framework OpenID/JWKS metadata before
normalizing the Activity into Caspian's provider-neutral schema.
"""

import base64
import json
import secrets
import time
from collections.abc import Mapping

import httpx
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.hashes import SHA256, Hash

from .base import (
    Attachment,
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)

CONNECTOR_BASE = "https://smba.trafficmanager.net/amer"
TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
OPENID_CONFIG_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
ISSUER = "https://api.botframework.com"
CONNECTOR_SCOPE = "https://api.botframework.com/.default"


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _message_id(conversation_id: str, activity_id: str) -> str:
    encoded_conversation = _b64url_encode(conversation_id.encode())
    return f"{encoded_conversation}:{activity_id}"


def _split_message_id(provider_message_id: str) -> tuple[str, str]:
    encoded_conversation, _, activity_id = provider_message_id.partition(":")
    return _b64url_decode(encoded_conversation).decode(), activity_id


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def parse_activity(data: dict, app_id: str) -> list[InboundMessage]:
    """Normalize a Bot Framework Activity into our inbound message schema."""
    if data.get("type") != "message":
        return []
    text = data.get("text")
    attachments = parse_attachments(data)
    if text is None and not attachments:
        return []
    conversation = data.get("conversation") or {}
    sender = data.get("from") or {}
    recipient = data.get("recipient") or {}
    conversation_id = conversation.get("id", "")
    activity_id = data.get("id", "")
    return [
        InboundMessage(
            external_event_id=f"{conversation_id}:{activity_id}",
            provider_inbox_id=app_id,
            provider_message_id=_message_id(conversation_id, activity_id),
            provider_thread_id=conversation_id,
            sender_address=sender.get("id"),
            sender_name=sender.get("name"),
            recipients=[{"id": recipient.get("id"), "name": recipient.get("name")}],
            text=text,
            html=data.get("textFormat") == "xml" and text or None,
            chat_type=_chat_type(conversation),
            attachments=attachments,
        )
    ]


def parse_attachments(data: dict) -> list[Attachment]:
    out: list[Attachment] = []
    for attachment in data.get("attachments") or []:
        out.append(
            Attachment(
                url=attachment.get("contentUrl"),
                mime_type=attachment.get("contentType"),
                filename=attachment.get("name"),
                provider_file_id=attachment.get("id"),
            )
        )
    return out


def _chat_type(conversation: Mapping[str, object]) -> str | None:
    if conversation.get("isGroup") is True:
        return "group"
    return "private" if conversation.get("conversationType") == "personal" else "channel"


class BotFrameworkJwtVerifier:
    """Small RS256 JWT verifier for Bot Framework Connector tokens."""

    def __init__(self, openid_config_url: str = OPENID_CONFIG_URL) -> None:
        self._openid_config_url = openid_config_url
        self._client = httpx.Client(timeout=30.0)
        self._jwks: dict | None = None
        self._jwks_url = ""

    def verify(self, token: str, audience: str) -> dict:
        header, claims, signing_input, signature = self._decode(token)
        if header.get("alg") != "RS256":
            raise WebhookVerificationError("Bot Framework JWT algorithm mismatch")
        if claims.get("iss") != ISSUER:
            raise WebhookVerificationError("Bot Framework JWT issuer mismatch")
        if claims.get("aud") != audience:
            raise WebhookVerificationError("Bot Framework JWT audience mismatch")
        now = int(time.time())
        if int(claims.get("nbf", 0)) > now or int(claims.get("exp", 0)) <= now:
            raise WebhookVerificationError("Bot Framework JWT expired or not yet valid")
        key = self._key(header.get("kid", ""))
        digest = Hash(SHA256())
        digest.update(signing_input)
        hashed = digest.finalize()
        try:
            key.verify(signature, hashed, padding.PKCS1v15(), Prehashed(SHA256()))
        except Exception as exc:
            raise WebhookVerificationError("Bot Framework JWT signature mismatch") from exc
        return claims

    def _decode(self, token: str) -> tuple[dict, dict, bytes, bytes]:
        try:
            head, body, sig = token.split(".")
            header = json.loads(_b64url_decode(head))
            claims = json.loads(_b64url_decode(body))
            signature = _b64url_decode(sig)
        except Exception as exc:
            raise WebhookVerificationError("invalid Bot Framework JWT") from exc
        return header, claims, f"{head}.{body}".encode(), signature

    def _key(self, kid: str):
        for jwk in self._jwks_doc().get("keys", []):
            if jwk.get("kid") == kid and jwk.get("kty") == "RSA":
                public_numbers = rsa.RSAPublicNumbers(
                    e=int.from_bytes(_b64url_decode(jwk["e"]), "big"),
                    n=int.from_bytes(_b64url_decode(jwk["n"]), "big"),
                )
                return public_numbers.public_key()
        raise WebhookVerificationError("Bot Framework JWT key not found")

    def _jwks_doc(self) -> dict:
        if self._jwks is not None:
            return self._jwks
        config = self._client.get(self._openid_config_url)
        config.raise_for_status()
        self._jwks_url = config.json()["jwks_uri"]
        jwks = self._client.get(self._jwks_url)
        jwks.raise_for_status()
        self._jwks = jwks.json()
        return self._jwks


class TeamsProvider:
    name = "teams"
    channel = "teams"
    connect_credentials = ("app_id", "app_password")
    capabilities = frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND, Capability.ATTACHMENTS}
    )

    def __init__(
        self,
        messaging_endpoint: str = "",
        connector_base_url: str = CONNECTOR_BASE,
        token_url: str = TOKEN_URL,
        openid_config_url: str = OPENID_CONFIG_URL,
        verifier: BotFrameworkJwtVerifier | None = None,
    ) -> None:
        self._messaging_endpoint = messaging_endpoint
        self._connector_base_url = connector_base_url.rstrip("/")
        self._token_url = token_url
        self._client = httpx.Client(timeout=30.0)
        self._verifier = verifier or BotFrameworkJwtVerifier(openid_config_url)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        app_id = request.credentials["app_id"]
        address = self._messaging_endpoint or f"teams:{app_id}"
        return ProvisionResult(address=address, provider_resource_id=app_id)

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        conversation_id = message.to[0]
        return self._post_activity(provider_inbox_id, conversation_id, message, credentials)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        conversation_id, activity_id = _split_message_id(provider_message_id)
        return self._post_activity(
            provider_inbox_id, conversation_id, message, credentials, reply_to_id=activity_id
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        app_id = (credentials or {}).get("app_id")
        if not app_id:
            raise WebhookVerificationError("teams webhooks require app_id credentials")
        self._verify_authorization(headers, app_id)
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_activity(data, app_id)

    def _verify_authorization(self, headers: Mapping[str, str], app_id: str) -> None:
        auth = lower_headers(headers).get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise WebhookVerificationError("missing Bot Framework bearer token")
        self._verifier.verify(token, app_id)

    def _post_activity(
        self,
        provider_inbox_id: str,
        conversation_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None,
        reply_to_id: str | None = None,
    ) -> SendResult:
        token = self._access_token(credentials)
        body: dict = {"type": "message", "text": message.text or ""}
        if reply_to_id:
            body["replyToId"] = reply_to_id
        if message.attachments:
            body["attachments"] = [
                {"contentType": a.mime_type, "contentUrl": a.url, "name": a.filename}
                for a in message.attachments
            ]
        response = self._client.post(
            f"{self._connector_base_url}/v3/conversations/{conversation_id}/activities",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        data = response.json()
        activity_id = data.get("id") or secrets.token_hex(8)
        return SendResult(
            provider_message_id=_message_id(conversation_id, activity_id),
            provider_thread_id=conversation_id,
        )

    def _access_token(self, credentials: Mapping[str, str] | None) -> str:
        creds = credentials or {}
        if creds.get("access_token"):
            return creds["access_token"]
        if not (creds.get("app_id") and creds.get("app_password")):
            raise RuntimeError("teams outbound requires app_id and app_password credentials")
        response = self._client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": creds["app_id"],
                "client_secret": creds["app_password"],
                "scope": CONNECTOR_SCOPE,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


class FakeTeamsProvider:
    name = "fake-teams"
    channel = "teams"
    capabilities = TeamsProvider.capabilities
    connect_credentials = ()
    optional_connect_credentials = ("app_id",)

    def __init__(self) -> None:
        self.app_id = f"fake-app-{secrets.token_hex(4)}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def _app_id(self, credentials: Mapping[str, str] | None) -> str:
        return (credentials or {}).get("app_id") or self.app_id

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        app_id = self._app_id(request.credentials)
        return ProvisionResult(address=f"teams:{app_id}", provider_resource_id=app_id)

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        conversation_id = message.to[0]
        self.sent.append({"conversation": conversation_id, "text": message.text})
        return SendResult(
            provider_message_id=_message_id(conversation_id, f"fake-{self._next()}"),
            provider_thread_id=conversation_id,
        )

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        conversation_id, activity_id = _split_message_id(provider_message_id)
        self.replies.append(
            {"conversation": conversation_id, "reply_to": activity_id, "text": message.text}
        )
        return SendResult(
            provider_message_id=_message_id(conversation_id, f"fake-{self._next()}"),
            provider_thread_id=conversation_id,
        )

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_activity(data, self._app_id(credentials))

    def webhook_payload(self, *, conversation_id="19:abc@thread.tacv2", text="Hi there"):
        return teams_message_activity(conversation_id, text, activity_id=f"msg-{self._next()}")

    def _next(self) -> int:
        self._seq += 1
        return self._seq


def teams_message_activity(
    conversation_id: str = "19:abc@thread.tacv2",
    text: str = "Hi there",
    activity_id: str = "1690000000000",
) -> dict:
    return {
        "type": "message",
        "id": activity_id,
        "timestamp": "2026-07-24T12:00:00.000Z",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "channelId": "msteams",
        "from": {"id": "29:user", "name": "Alice Ng"},
        "conversation": {"id": conversation_id, "conversationType": "channel", "isGroup": True},
        "recipient": {"id": "28:bot", "name": "Caspian Bot"},
        "textFormat": "plain",
        "text": text,
    }
