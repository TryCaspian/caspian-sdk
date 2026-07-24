"""In-memory Telegram provider for local development and tests.

Consumes real Telegram Update shapes so tests exercise the same
normalization path as the live adapter. Zero-config by default; supply a
bot_token credential to exercise the multi-tenant per-bot path exactly as
the live adapter requires it.
"""

import hmac
import json
import secrets
from collections.abc import Mapping

from .base import (
    InboundEvent,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)
from .telegram import SECRET_HEADER, TelegramProvider, bot_id_from_token, parse_update


class FakeTelegramProvider:
    name = "fake-telegram"
    channel = "telegram"
    capabilities = TelegramProvider.capabilities
    connect_credentials = ()
    # zero-config for tests, but honors a bot_token when one is supplied so
    # the multi-tenant per-bot path is exercised exactly like the live adapter
    optional_connect_credentials = ("bot_token",)

    def __init__(self, webhook_secret: str = "") -> None:
        self.bot_id = str(9_000_000 + secrets.randbelow(1_000_000))
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._webhook_secret = webhook_secret
        self._update_seq = 0

    def _bot_id(self, credentials: Mapping[str, str] | None) -> str:
        token = (credentials or {}).get("bot_token")
        return bot_id_from_token(token) if token else self.bot_id

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"@fake_{request.agent_id[-6:]}_bot",
            provider_resource_id=self._bot_id(request.credentials),
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        chat_id = message.to[0]
        self.sent.append({"bot_id": provider_inbox_id, "chat_id": chat_id, "text": message.text})
        return SendResult(
            provider_message_id=f"{chat_id}:{secrets.randbelow(100000)}",
            provider_thread_id=str(chat_id),
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        chat_id, _, target_message_id = provider_message_id.partition(":")
        self.replies.append(
            {
                "bot_id": provider_inbox_id,
                "chat_id": chat_id,
                "in_reply_to": target_message_id,
                "text": message.text,
            }
        )
        return SendResult(
            provider_message_id=f"{chat_id}:{secrets.randbelow(100000)}",
            provider_thread_id=chat_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundEvent]:
        secret = (credentials or {}).get("webhook_secret") or self._webhook_secret
        if secret:
            received = lower_headers(headers).get(SECRET_HEADER) or ""
            if not hmac.compare_digest(received, secret):
                raise WebhookVerificationError("secret token mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_update(data, self._bot_id(credentials))

    def webhook_payload(
        self,
        *,
        chat_id: int = 4242,
        text: str = "Hi there",
        sender_username: str = "customer",
        sender_first_name: str = "Customer",
        update_id: int | None = None,
        message_id: int | None = None,
    ) -> dict:
        self._update_seq += 1
        return {
            "update_id": update_id if update_id is not None else 100_000 + self._update_seq,
            "message": {
                "message_id": message_id if message_id is not None else self._update_seq,
                "from": {
                    "id": 555_000 + self._update_seq,
                    "username": sender_username,
                    "first_name": sender_first_name,
                },
                "chat": {"id": chat_id, "type": "private"},
                "date": 1_752_400_000,
                "text": text,
            },
        }
