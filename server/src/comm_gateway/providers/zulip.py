"""Zulip adapter (outgoing webhook bot).

Zulip supports outgoing webhooks: when a bot is mentioned in a stream or
receives a direct message, Zulip POSTs a JSON payload to the configured
URL. The adapter verifies the token from the JSON body before processing.

- provision returns a synthetic address (@botname on the Zulip realm)
- provider_thread_id is the Zulip stream + topic, encoded as "stream_id:topic"
  (for DMs: a sorted participant key so group DMs are distinct)
- provider_message_id is "stream_id:message_id" (composite, never leaves this package)
- outbound uses the Zulip REST API: POST /api/v1/messages
"""

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
    split_composite_id,
)


def _dm_thread_key(message: dict) -> str:
    """Build a canonical thread key for a private message from its participant
    list so that group DMs with different senders are distinct threads."""
    recipients = message.get("display_recipient")
    if isinstance(recipients, list):
        ids = sorted(str(r.get("id", "")) for r in recipients)
        return "dm:" + ",".join(ids)
    return f"dm:{message.get('sender_id', 'unknown')}"


def parse_outgoing_webhook(data: dict, bot_email: str) -> list[InboundMessage]:
    """Normalize a Zulip outgoing-webhook payload into our schema.

    Handles both stream and direct messages. Skips payloads without a
    message body or required fields (e.g. test pings).
    """
    message = data.get("message")
    if not isinstance(message, dict) or message.get("content") is None:
        return []
    if "id" not in message:
        return []

    msg_type = message.get("type", "stream")
    message_id = str(message["id"])

    if msg_type == "private":
        thread_key = _dm_thread_key(message)
        stream_id = thread_key
        chat_type = "private"
    else:
        stream_id = str(message.get("stream_id", message.get("display_recipient", "")))
        thread_key = f"{stream_id}:{message.get('subject', '')}"
        chat_type = "channel"

    return [
        InboundMessage(
            external_event_id=f"{bot_email}:{message_id}",
            provider_inbox_id=bot_email,
            provider_message_id=f"{stream_id}:{message_id}",
            provider_thread_id=thread_key,
            sender_address=message.get("sender_email"),
            sender_name=message.get("sender_full_name"),
            text=message["content"],
            chat_type=chat_type,
        )
    ]


class ZulipProvider:
    name = "zulip"
    channel = "zulip"
    connect_credentials = ("bot_email", "bot_api_key")
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    def __init__(
        self,
        webhook_token: str = "",
        base_url: str = "",
    ) -> None:
        self._webhook_token = webhook_token
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    @staticmethod
    def _creds(credentials: Mapping[str, str] | None) -> tuple[str, str]:
        creds = credentials or {}
        email = creds.get("bot_email", "")
        api_key = creds.get("bot_api_key", "")
        if not email or not api_key:
            raise ValueError("connection is missing bot_email and bot_api_key credentials")
        return email, api_key

    def _api_url(self, credentials: Mapping[str, str] | None) -> str:
        """Resolve the Zulip server URL from credentials or the deployment default."""
        server = (credentials or {}).get("server_url", "") or self._base_url
        if not server:
            raise ValueError("no Zulip server URL configured (set server_url or COMM_ZULIP_BASE_URL)")
        return server.rstrip("/")

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        email, api_key = self._creds(request.credentials)
        server = self._api_url(request.credentials)
        response = self._client.get(
            f"{server}/api/v1/users/me",
            auth=(email, api_key),
        )
        response.raise_for_status()
        me = response.json()
        return ProvisionResult(
            address=me.get("user", {}).get("email", email),
            provider_resource_id=str(me.get("user", {}).get("user_id", email)),
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        email, api_key = self._creds(credentials)
        server = self._api_url(credentials)
        dest = message.to[0] if message.to else ""
        stream_id, topic = split_composite_id(dest)
        response = self._client.post(
            f"{server}/api/v1/messages",
            auth=(email, api_key),
            data={
                "type": "stream",
                "to": stream_id,
                "topic": topic or "(no topic)",
                "content": message.text or "",
            },
        )
        response.raise_for_status()
        result = response.json()
        mid = str(result.get("id", ""))
        return SendResult(
            provider_message_id=f"{stream_id}:{mid}",
            provider_thread_id=f"{stream_id}:{topic}",
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        email, api_key = self._creds(credentials)
        server = self._api_url(credentials)
        stream_id, target_id = split_composite_id(provider_message_id)

        # Fetch the original message to determine type (stream vs DM) and topic
        response = self._client.get(
            f"{server}/api/v1/messages/{target_id}",
            auth=(email, api_key),
        )
        if response.status_code != 200:
            raise ValueError(f"could not fetch original message {target_id}")

        orig = response.json().get("message", {})
        orig_type = orig.get("type", "stream")

        if orig_type == "private":
            # Reply to a DM: use type="direct" with the participant list
            recipients = orig.get("display_recipient", [])
            to_ids = [r["id"] for r in recipients if isinstance(r, dict) and "id" in r]
            response = self._client.post(
                f"{server}/api/v1/messages",
                auth=(email, api_key),
                data={
                    "type": "direct",
                    "to": json.dumps(to_ids),
                    "content": message.text or "",
                },
            )
        else:
            topic = orig.get("subject", "(no topic)")
            response = self._client.post(
                f"{server}/api/v1/messages",
                auth=(email, api_key),
                data={
                    "type": "stream",
                    "to": stream_id,
                    "topic": topic,
                    "content": message.text or "",
                },
            )

        response.raise_for_status()
        result = response.json()
        mid = str(result.get("id", ""))
        return SendResult(
            provider_message_id=f"{stream_id}:{mid}",
            provider_thread_id=stream_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc

        if not isinstance(data, dict):
            raise WebhookVerificationError("invalid JSON payload")

        # Zulip sends the bot token inside the JSON body, not as a header.
        # Verify it against the configured webhook_token.
        token = (credentials or {}).get("webhook_token") or self._webhook_token
        if token:
            received = data.get("token", "")
            if not isinstance(received, str) or not hmac.compare_digest(received, token):
                raise WebhookVerificationError("bot token mismatch")
        elif not credentials:
            raise WebhookVerificationError("zulip webhooks require a bot token or connection scope")

        bot_email = (credentials or {}).get("bot_email", "") or data.get("bot_email", "")
        return parse_outgoing_webhook(data, bot_email)
