"""Zulip adapter (outgoing-webhook bot inbound, REST API outbound).

An agent is a Zulip outgoing-webhook bot. Inbound arrives when a user
@-mentions the bot in a stream or sends it a direct message — Zulip POSTs
the message to the configured endpoint. Outbound uses POST /api/v1/messages
with HTTP Basic auth (bot_email:api_key).

provider_thread_id is "{stream_id}:{topic}" for stream messages or
"dm:{recipient_id}" for direct messages. provider_message_id is
"{thread_id}:{message_id}" so a reply can address the right conversation.
"""

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
    split_composite_id,
)


def parse_webhook_event(data: dict) -> list[InboundMessage]:
    """Normalize a Zulip outgoing-webhook payload into our schema."""
    message = data.get("message")
    if not message:
        return []

    msg_id = message.get("id")
    sender_email = message.get("sender_email", "")
    sender_name = message.get("sender_full_name", "")
    text = message.get("content") or data.get("data") or ""
    msg_type = message.get("type", "")
    bot_email = data.get("bot_email", "")

    if msg_type == "stream":
        stream_id = message.get("stream_id", "")
        topic = message.get("subject", "")
        thread_id = f"{stream_id}:{topic}"
        chat_type = "channel"
    else:
        recipient_id = message.get("recipient_id", "")
        thread_id = f"dm:{recipient_id}"
        chat_type = "private"

    provider_message_id = f"{thread_id}:{msg_id}"

    return [
        InboundMessage(
            external_event_id=f"{bot_email}:{msg_id}",
            provider_inbox_id=bot_email,
            provider_message_id=provider_message_id,
            provider_thread_id=thread_id,
            sender_address=sender_email,
            sender_name=sender_name,
            text=text,
            chat_type=chat_type,
        )
    ]


class ZulipProvider:
    name = "zulip"
    channel = "zulip"
    connect_credentials = ("bot_email", "bot_api_key", "server_url")
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    def __init__(self, webhook_base: str = "") -> None:
        self._webhook_base = webhook_base

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        bot_email = creds.get("bot_email", "")
        return ProvisionResult(
            address=bot_email or f"zulip-bot-{request.agent_id[-6:]}",
            provider_resource_id=bot_email,
        )

    def _api(self, credentials: Mapping[str, str] | None) -> tuple[httpx.Client, str]:
        creds = credentials or {}
        server_url = creds.get("server_url", "").rstrip("/")
        client = httpx.Client(
            base_url=f"{server_url}/api/v1",
            auth=(creds.get("bot_email", ""), creds.get("bot_api_key", "")),
            timeout=30.0,
        )
        return client, creds.get("bot_email", "")

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        client, _ = self._api(credentials)
        to = message.to[0]

        if to.startswith("dm:"):
            body = {
                "type": "direct",
                "to": json.dumps([int(to.removeprefix("dm:"))]),
                "content": message.text or "",
            }
        else:
            stream_id, _, topic = to.partition(":")
            body = {
                "type": "stream",
                "to": stream_id,
                "topic": topic or "agent",
                "content": message.text or "",
            }

        r = client.post("/messages", data=body)
        r.raise_for_status()
        data = r.json()
        if data.get("result") != "success":
            raise RuntimeError(f"Zulip send failed: {data.get('msg')}")
        mid = data["id"]
        return SendResult(
            provider_message_id=f"{to}:{mid}",
            provider_thread_id=to,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        thread_id, _ = split_composite_id(provider_message_id)
        client, _ = self._api(credentials)

        if thread_id.startswith("dm:"):
            recipient_id = thread_id.removeprefix("dm:")
            body = {
                "type": "direct",
                "to": json.dumps([int(recipient_id)]),
                "content": message.text or "",
            }
        else:
            stream_id, _, topic = thread_id.partition(":")
            body = {
                "type": "stream",
                "to": stream_id,
                "topic": topic or "agent",
                "content": message.text or "",
            }

        r = client.post("/messages", data=body)
        r.raise_for_status()
        data = r.json()
        if data.get("result") != "success":
            raise RuntimeError(f"Zulip reply failed: {data.get('msg')}")
        mid = data["id"]
        return SendResult(
            provider_message_id=f"{thread_id}:{mid}",
            provider_thread_id=thread_id,
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

        expected_token = (credentials or {}).get("bot_token", "")
        if expected_token:
            received_token = data.get("token", "")
            if received_token != expected_token:
                raise WebhookVerificationError("Zulip token mismatch")

        return parse_webhook_event(data)
