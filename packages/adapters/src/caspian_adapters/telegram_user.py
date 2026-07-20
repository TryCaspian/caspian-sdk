"""Telegram user-account transport (MTProto) — OPT-IN secondary.

The Bot API cannot cold-start a conversation, read history, see presence, or
join groups on its own. A Telegram *user account* (the official MTProto API,
api_id/api_hash) can. This transport exposes that superset for teams that
explicitly opt in.

Trade-offs the caller accepts by enabling it:
- It automates a real user account. Telegram publishes this API and tolerates
  userbots, but mass/spammy automation can still get an account limited or
  banned. This is never the default; Bot API stays primary.
- It authenticates with a pre-created session string (phone-number + OTP login
  happens once, out of band) supplied via COMM_TELEGRAM_USER_SESSION.
- Inbound arrives over a long-lived MTProto connection, not webhooks, so a
  user-account connection runs its own listener rather than the shared webhook
  route. That listener is intentionally out of scope for this slice; the
  send/initiate/backfill surface below is what PR wires up.

This module imports telethon lazily so the package works without it installed.
The real client paths are integration-tested separately, not by the unit
suite — the fake (fake_telegram_user.py) covers the gateway wiring.
"""

from collections.abc import Mapping

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    split_composite_id,
)

# Everything a user account can do that the Bot API cannot, plus the basics.
# Note: SECRET_CHATS is deliberately absent — end-to-end secret chats are
# device-bound and not exposed by MTProto client libraries, so we do not
# pretend to support them.
USER_CAPABILITIES = frozenset(
    {
        Capability.RECEIVE,
        Capability.REPLY,
        Capability.SEND,
        Capability.INITIATE,
        Capability.GROUP_VISIBILITY,
        Capability.EDIT_INBOUND,
        Capability.BACKFILL,
        Capability.PRESENCE,
        Capability.READ_RECEIPTS,
        Capability.AUTO_JOIN,
        Capability.SEE_BOTS,
    }
)


class TelegramUserProvider:
    name = "telegram-user"
    channel = "telegram"
    capabilities = USER_CAPABILITIES

    def __init__(self, session: str, api_id: int, api_hash: str) -> None:
        if not (session and api_id and api_hash):
            raise ValueError(
                "telegram-user requires COMM_TELEGRAM_USER_SESSION, "
                "COMM_TELEGRAM_API_ID and COMM_TELEGRAM_API_HASH"
            )
        self._session = session
        self._api_id = api_id
        self._api_hash = api_hash
        self._client = None  # lazily built telethon TelegramClient

    def _telethon(self):
        if self._client is None:
            from telethon.sessions import StringSession  # noqa: PLC0415
            from telethon.sync import TelegramClient  # noqa: PLC0415

            self._client = TelegramClient(
                StringSession(self._session), self._api_id, self._api_hash
            )
            self._client.connect()
        return self._client

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        client = self._telethon()
        me = client.get_me()
        username = f"@{me.username}" if me.username else str(me.id)
        return ProvisionResult(address=username, provider_resource_id=str(me.id))

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        """Cold-start a conversation with a recipient (@username or phone)."""
        client = self._telethon()
        sent = client.send_message(recipient, message.text or "")
        chat_id = str(sent.chat_id)
        return SendResult(
            provider_message_id=f"{chat_id}:{sent.id}", provider_thread_id=chat_id
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        client = self._telethon()
        chat_id = message.to[0]
        sent = client.send_message(int(chat_id), message.text or "")
        return SendResult(
            provider_message_id=f"{chat_id}:{sent.id}", provider_thread_id=chat_id
        )

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        client = self._telethon()
        chat_id, target = split_composite_id(provider_message_id)
        sent = client.send_message(int(chat_id), message.text or "", reply_to=int(target))
        return SendResult(
            provider_message_id=f"{chat_id}:{sent.id}", provider_thread_id=chat_id
        )

    def backfill(
        self, provider_inbox_id: str, thread_id: str, limit: int,
        credentials=None,
    ) -> list[InboundMessage]:
        """Fetch history from before the connection existed, oldest to newest."""
        client = self._telethon()
        out: list[InboundMessage] = []
        for msg in reversed(client.get_messages(int(thread_id), limit=limit)):
            if not msg.text:
                continue
            sender = msg.sender
            out.append(
                InboundMessage(
                    external_event_id=f"{thread_id}:{msg.id}",
                    provider_inbox_id=provider_inbox_id,
                    provider_message_id=f"{thread_id}:{msg.id}",
                    provider_thread_id=str(thread_id),
                    sender_address=getattr(sender, "username", None),
                    sender_name=getattr(sender, "first_name", None),
                    text=msg.text,
                    chat_type="private",
                )
            )
        return out

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        # User accounts receive over MTProto, not webhooks. A dedicated listener
        # (out of scope for this slice) feeds inbound; the webhook path is unused.
        raise NotImplementedError("telegram-user delivers inbound over MTProto, not webhooks")
