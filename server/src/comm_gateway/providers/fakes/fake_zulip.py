"""In-memory Zulip provider for local development and tests.

Consumes real Zulip outgoing-webhook shapes so tests exercise the same
normalization path as the live adapter. Zero-config by default.
"""

import hmac
import json
import secrets
from collections.abc import Mapping

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
    split_composite_id,
)
from ..zulip import ZulipProvider, parse_outgoing_webhook


class FakeZulipProvider:
    name = "fake-zulip"
    channel = "zulip"
    capabilities = ZulipProvider.capabilities
    connect_credentials = ()

    def __init__(self, webhook_token: str = "") -> None:
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._webhook_token = webhook_token
        self._msg_seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"bot-{request.agent_id[-6:]}@fake-zulip.local",
            provider_resource_id=f"fake-{secrets.randbelow(100000)}",
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        dest = message.to[0] if message.to else ""
        stream_id, topic = split_composite_id(dest)
        self._msg_seq += 1
        mid = str(self._msg_seq)
        self.sent.append({"stream_id": stream_id, "topic": topic, "text": message.text})
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
        stream_id, target_id = split_composite_id(provider_message_id)
        self._msg_seq += 1
        mid = str(self._msg_seq)
        self.replies.append({
            "stream_id": stream_id,
            "in_reply_to": target_id,
            "text": message.text,
        })
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
        token = (credentials or {}).get("webhook_token") or self._webhook_token
        if token:
            received = data.get("token", "")
            if not isinstance(received, str) or not hmac.compare_digest(received, token):
                raise WebhookVerificationError("bot token mismatch")
        bot_email = (credentials or {}).get("bot_email", "") or data.get("bot_email", "")
        return parse_outgoing_webhook(data, bot_email)

    def webhook_payload(
        self,
        *,
        stream_id: int = 101,
        topic: str = "general",
        text: str = "Hello from Zulip",
        sender_email: str = "user@zulip.local",
        sender_name: str = "Test User",
        message_id: int | None = None,
        msg_type: str = "stream",
    ) -> dict:
        self._msg_seq += 1
        mid = message_id if message_id is not None else 5000 + self._msg_seq
        msg: dict = {
            "id": mid,
            "content": text,
            "sender_email": sender_email,
            "sender_full_name": sender_name,
            "type": msg_type,
            "subject": topic,
        }
        if msg_type == "stream":
            msg["stream_id"] = stream_id
            msg["display_recipient"] = "general"
        else:
            msg["sender_id"] = 42
            msg["display_recipient"] = [
                {"id": 42, "email": sender_email},
                {"id": 99, "email": "bot@zulip.local"},
            ]
        return {"message": msg, "bot_email": "bot@zulip.local", "token": "test-token"}
