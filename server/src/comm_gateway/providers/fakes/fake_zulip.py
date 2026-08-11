"""In-memory Zulip provider for local development and tests.

Consumes real Zulip outgoing-webhook payload shapes so tests exercise the
same normalization path as the live adapter.
"""

import json
from collections.abc import Mapping

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from ..zulip import ZulipProvider, parse_webhook_event


class FakeZulipProvider:
    name = "fake-zulip"
    channel = "zulip"
    capabilities = ZulipProvider.capabilities
    connect_credentials = ()

    BOT_EMAIL = "bot@zulip.test"

    def __init__(self, webhook_token: str = "") -> None:
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._webhook_token = webhook_token
        self._msg_seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=self.BOT_EMAIL,
            provider_resource_id=self.BOT_EMAIL,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        to = message.to[0]
        self._msg_seq += 1
        mid = 50_000 + self._msg_seq
        self.sent.append({"to": to, "text": message.text})
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
        thread_id, _, orig_msg_id = provider_message_id.partition(":")
        self._msg_seq += 1
        mid = 50_000 + self._msg_seq
        self.replies.append({
            "thread_id": thread_id,
            "in_reply_to": orig_msg_id,
            "text": message.text,
        })
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
        if self._webhook_token:
            try:
                data = json.loads(payload)
            except ValueError as exc:
                raise WebhookVerificationError("invalid JSON payload") from exc
            if data.get("token", "") != self._webhook_token:
                raise WebhookVerificationError("Zulip token mismatch")
            return parse_webhook_event(data)

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_webhook_event(data)

    def webhook_payload(
        self,
        *,
        msg_id: int | None = None,
        stream_id: int = 5,
        topic: str = "general",
        text: str = "Hello bot",
        sender_email: str = "user@zulip.test",
        sender_name: str = "Test User",
        bot_email: str = "bot@zulip.test",
        trigger: str = "mention",
        token: str = "test_token_abc",
    ) -> dict:
        """A Zulip outgoing-webhook payload for a stream message."""
        self._msg_seq += 1
        mid = msg_id if msg_id is not None else 1000 + self._msg_seq
        return {
            "bot_email": bot_email,
            "bot_full_name": "Test Bot",
            "data": text,
            "token": token,
            "trigger": trigger,
            "message": {
                "id": mid,
                "type": "stream",
                "content": text,
                "rendered_content": f"<p>{text}</p>",
                "sender_email": sender_email,
                "sender_full_name": sender_name,
                "sender_id": 100,
                "stream_id": stream_id,
                "display_recipient": "test-stream",
                "subject": topic,
                "timestamp": 1_752_400_000,
                "recipient_id": 20,
                "is_me_message": False,
                "reactions": [],
                "topic_links": [],
                "submessages": [],
            },
        }

    def dm_payload(
        self,
        *,
        msg_id: int | None = None,
        recipient_id: int = 42,
        text: str = "Hello bot",
        sender_email: str = "user@zulip.test",
        sender_name: str = "Test User",
        bot_email: str = "bot@zulip.test",
        token: str = "test_token_abc",
    ) -> dict:
        """A Zulip outgoing-webhook payload for a direct message."""
        self._msg_seq += 1
        mid = msg_id if msg_id is not None else 1000 + self._msg_seq
        return {
            "bot_email": bot_email,
            "bot_full_name": "Test Bot",
            "data": text,
            "token": token,
            "trigger": "direct_message",
            "message": {
                "id": mid,
                "type": "private",
                "content": text,
                "rendered_content": f"<p>{text}</p>",
                "sender_email": sender_email,
                "sender_full_name": sender_name,
                "sender_id": 100,
                "display_recipient": [
                    {"id": 100, "email": sender_email,
                     "full_name": sender_name, "is_mirror_dummy": False},
                    {"id": 200, "email": bot_email,
                     "full_name": "Test Bot", "is_mirror_dummy": False},
                ],
                "subject": "",
                "timestamp": 1_752_400_000,
                "recipient_id": recipient_id,
                "is_me_message": False,
                "reactions": [],
                "topic_links": [],
                "submessages": [],
            },
        }
