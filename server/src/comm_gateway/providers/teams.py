"""Microsoft Teams adapter (Bot Framework).

Uses the Azure Bot Service / Bot Framework: Teams sends Activity objects to
the configured messaging endpoint. Outbound replies go through the Bot
Connector REST API (POST /v3/conversations/{id}/activities).

- provision resolves the bot identity via the Bot Framework token endpoint
- provider_thread_id is the Teams conversation id
- provider_message_id is "{conversation_id}|{activity_id}" (pipe-separated
  because Teams conversation ids contain colons, e.g. 19:abc@thread.tacv2)
- inbound verification uses HMAC-SHA256 of the raw payload with the app
  secret, carried in the X-Teams-Signature header (the gateway layer handles
  the upstream JWT validation from Microsoft and injects this header)
"""

import hashlib
import hmac
import json
from collections.abc import Mapping

import httpx

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)

SIGNATURE_HEADER = "x-teams-signature"
BOT_FRAMEWORK_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
BOT_CONNECTOR_BASE = "https://smba.trafficmanager.net/teams"

# Teams conversation ids contain colons (e.g. "19:abc@thread.tacv2"), so we
# use pipe as the composite-id separator instead of the default colon.
COMPOSITE_SEP = "|"


def _split_teams_id(mid: str) -> tuple[str, str]:
    """Split a pipe-separated composite id into (conversation_id, activity_id)."""
    head, _, tail = mid.partition(COMPOSITE_SEP)
    return head, tail


def parse_activity(data: dict, bot_id: str) -> list[InboundMessage]:
    """Normalize a Bot Framework Activity into our schema.

    Only processes message activities (type == "message"). Skips typing
    indicators, conversation updates, and other activity types.
    """
    activity_type = data.get("type", "")
    if activity_type != "message":
        return []

    text = data.get("text")
    if text is None:
        return []

    conversation = data.get("conversation", {})
    conversation_id = conversation.get("id", "")
    activity_id = data.get("id", "")
    from_field = data.get("from", {})
    sender_id = from_field.get("id", "")
    sender_name = from_field.get("name")

    # Strip the bot @mention prefix that Teams prepends to messages
    recipient = data.get("recipient", {})
    bot_name = recipient.get("name", "")
    if bot_name and text.startswith(f"<at>{bot_name}</at>"):
        text = text[len(f"<at>{bot_name}</at>"):].strip()

    chat_type = "private"
    if conversation.get("isGroup") or conversation.get("conversationType") == "channel":
        chat_type = "group"

    return [
        InboundMessage(
            external_event_id=f"{bot_id}:{activity_id}",
            provider_inbox_id=bot_id,
            provider_message_id=f"{conversation_id}{COMPOSITE_SEP}{activity_id}",
            provider_thread_id=conversation_id,
            sender_address=sender_id,
            sender_name=sender_name,
            text=text,
            chat_type=chat_type,
        )
    ]


class TeamsProvider:
    name = "teams"
    channel = "teams"
    connect_credentials = ("app_id", "app_secret")
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        base_url: str = BOT_CONNECTOR_BASE,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    @staticmethod
    def _creds(credentials: Mapping[str, str] | None) -> tuple[str, str]:
        creds = credentials or {}
        app_id = creds.get("app_id", "")
        app_secret = creds.get("app_secret", "")
        if not app_id or not app_secret:
            raise ValueError("connection is missing app_id and app_secret credentials")
        return app_id, app_secret

    def _get_token(self, app_id: str, app_secret: str) -> str:
        """Obtain a Bot Framework OAuth token for API calls."""
        response = self._client.post(
            BOT_FRAMEWORK_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_secret,
                "scope": "https://api.botframework.com/.default",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        app_id, _ = self._creds(request.credentials)
        return ProvisionResult(
            address=f"teams-bot-{app_id[:8]}",
            provider_resource_id=app_id,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        app_id, app_secret = self._creds(credentials)
        token = self._get_token(app_id, app_secret)
        conversation_id = message.to[0] if message.to else ""
        response = self._client.post(
            f"{self._base_url}/v3/conversations/{conversation_id}/activities",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "type": "message",
                "text": message.text or "",
            },
        )
        response.raise_for_status()
        result = response.json()
        aid = result.get("id", "")
        return SendResult(
            provider_message_id=f"{conversation_id}{COMPOSITE_SEP}{aid}",
            provider_thread_id=conversation_id,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        app_id, app_secret = self._creds(credentials)
        token = self._get_token(app_id, app_secret)
        conversation_id, reply_to_id = _split_teams_id(provider_message_id)
        response = self._client.post(
            f"{self._base_url}/v3/conversations/{conversation_id}/activities",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "type": "message",
                "text": message.text or "",
                "replyToId": reply_to_id,
            },
        )
        response.raise_for_status()
        result = response.json()
        aid = result.get("id", "")
        return SendResult(
            provider_message_id=f"{conversation_id}{COMPOSITE_SEP}{aid}",
            provider_thread_id=conversation_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("app_secret") or self._app_secret
        if secret:
            h = lower_headers(headers)
            received = h.get(SIGNATURE_HEADER, "")
            expected = hmac.new(
                secret.encode(), payload, hashlib.sha256
            ).hexdigest()
            if not received or not hmac.compare_digest(received, expected):
                raise WebhookVerificationError("HMAC signature mismatch")
        elif not credentials:
            raise WebhookVerificationError("teams webhooks require an app_secret or connection scope")

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc

        app_id = (credentials or {}).get("app_id") or self._app_id
        return parse_activity(data, app_id)
