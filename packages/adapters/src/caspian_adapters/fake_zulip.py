"""In-memory Zulip provider using real outgoing-webhook payload shapes.

Consumes the same JSON Zulip POSTs to an outgoing-webhook bot, so the gateway
wiring is exercised on the exact normalization path as the live adapter — no
network, no realm required.
"""

import json

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from .zulip import ZulipProvider, _destination, parse_message

BOT_EMAIL = "agent-bot@acme.zulipchat.com"
WEBHOOK_TOKEN = "fake-outgoing-webhook-token"


class FakeZulipProvider:
    name = "fake-zulip"
    channel = "zulip"
    capabilities = ZulipProvider.capabilities
    connect_credentials = ZulipProvider.connect_credentials

    def __init__(self, bot_email: str = BOT_EMAIL, webhook_token: str = WEBHOOK_TOKEN) -> None:
        self.bot_email = bot_email
        self.webhook_token = webhook_token
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 4200

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        return ProvisionResult(
            address=creds.get("bot_email", self.bot_email),
            provider_resource_id=creds.get("provider_resource_id", "9001"),
        )

    def _result(self, routing: str, target: list[dict], text: str | None) -> SendResult:
        thread, fields = _destination(routing)
        self._seq += 1
        target.append({"thread_id": thread, "fields": fields, "text": text})
        return SendResult(
            provider_message_id=f"{thread}:{self._seq}", provider_thread_id=thread
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        if not message.to:
            raise ValueError("Zulip send requires a routing destination in message.to")
        return self._result(message.to[0], self.sent, message.text)

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        return self._result(provider_message_id, self.replies, message.text)

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON") from exc
        token = (credentials or {}).get("webhook_token", self.webhook_token)
        if token and str(data.get("token", "")) != str(token):
            raise WebhookVerificationError("Zulip token mismatch")
        return parse_message(data)

    def webhook_payload(
        self,
        *,
        text: str = "@**Agent** hello there",
        stream_id: int = 7,
        stream_name: str = "general",
        topic: str = "support",
        dm: bool = False,
        sender_email: str = "customer@acme.zulipchat.com",
        sender_full_name: str = "Casey Customer",
        sender_id: int = 55,
    ) -> dict:
        """Build a realistic Zulip outgoing-webhook payload (channel or DM)."""
        self._seq += 1
        if dm:
            message = {
                "id": self._seq,
                "type": "private",
                "content": text,
                "sender_id": sender_id,
                "sender_email": sender_email,
                "sender_full_name": sender_full_name,
                "display_recipient": [
                    {"id": sender_id, "email": sender_email, "full_name": sender_full_name},
                    {"id": 9001, "email": self.bot_email, "full_name": "Agent"},
                ],
            }
            trigger = "direct_message"
        else:
            message = {
                "id": self._seq,
                "type": "stream",
                "content": text,
                "sender_id": sender_id,
                "sender_email": sender_email,
                "sender_full_name": sender_full_name,
                "display_recipient": stream_name,
                "stream_id": stream_id,
                "subject": topic,
            }
            trigger = "mention"
        return {
            "bot_email": self.bot_email,
            "bot_full_name": "Agent",
            "data": text,
            "token": self.webhook_token,
            "trigger": trigger,
            "message": message,
        }
