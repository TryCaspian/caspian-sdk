"""In-memory Telegram user-account transport for tests.

Exercises the gateway wiring for the user-account capability superset
(initiate + backfill) without a live MTProto session. It seeds a small
history so backfill has something to return.
"""

import secrets

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from .telegram_user import USER_CAPABILITIES


class FakeTelegramUserProvider:
    name = "fake-telegram-user"
    channel = "telegram"
    capabilities = USER_CAPABILITIES

    def __init__(self) -> None:
        self.user_id = str(700_000 + secrets.randbelow(100_000))
        self.initiated: list[dict] = []
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        # Pretend history keyed by chat_id, oldest first.
        self.history: dict[str, list[dict]] = {}

    def seed_history(self, chat_id: int, texts: list[str]) -> None:
        self.history[str(chat_id)] = [
            {"id": i + 1, "text": text, "username": "olduser", "first_name": "Old"}
            for i, text in enumerate(texts)
        ]

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"@fake_user_{request.agent_id[-6:]}",
            provider_resource_id=self.user_id,
        )

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        chat_id = str(abs(hash(recipient)) % 1_000_000)
        self.initiated.append({"recipient": recipient, "chat_id": chat_id, "text": message.text})
        return SendResult(
            provider_message_id=f"{chat_id}:{secrets.randbelow(100000)}",
            provider_thread_id=chat_id,
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        chat_id = message.to[0]
        self.sent.append({"chat_id": chat_id, "text": message.text})
        return SendResult(
            provider_message_id=f"{chat_id}:{secrets.randbelow(100000)}",
            provider_thread_id=str(chat_id),
        )

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        chat_id, _, target = provider_message_id.partition(":")
        self.replies.append({"chat_id": chat_id, "in_reply_to": target, "text": message.text})
        return SendResult(
            provider_message_id=f"{chat_id}:{secrets.randbelow(100000)}",
            provider_thread_id=chat_id,
        )

    def backfill(
        self, provider_inbox_id: str, thread_id: str, limit: int,
        credentials=None,
    ) -> list[InboundMessage]:
        rows = self.history.get(str(thread_id), [])[:limit]
        return [
            InboundMessage(
                external_event_id=f"{thread_id}:{row['id']}",
                provider_inbox_id=provider_inbox_id,
                provider_message_id=f"{thread_id}:{row['id']}",
                provider_thread_id=str(thread_id),
                sender_address=row["username"],
                sender_name=row["first_name"],
                text=row["text"],
                chat_type="private",
            )
            for row in rows
        ]
