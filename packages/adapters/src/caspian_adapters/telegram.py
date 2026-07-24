"""Telegram adapter (official Bot API), one bot per connection.

Telegram has no API for creating bots, so each developer supplies their own
@BotFather token at connect time (the one step only a human can do). We do
the rest: the token is stored on the connection, the webhook is registered
at a per-bot path, and inbound updates route by bot id - developers and
their bots never overlap.

- provision resolves the bot via getMe; the connection address is @username
- the webhook URL is {webhook_base}/{bot_id}, verified with a per-connection
  secret_token Telegram echoes back in a header
- provider_thread_id is the Telegram chat id
- provider_message_id is "{chat_id}:{message_id}" so a reply can be routed
  without extra lookups (composite ids never leave this package)
"""

import hmac
import json
import re
from collections.abc import Mapping

import httpx

from .base import (
    Capability,
    InboundCommand,
    InboundEvent,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
    split_composite_id,
)

SECRET_HEADER = "x-telegram-bot-api-secret-token"


def bot_id_from_token(token: str) -> str:
    return token.split(":", 1)[0]


COMMAND_RE = re.compile(r"^/([A-Za-z0-9_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)


def parse_update(data: dict, bot_id: str) -> list[InboundEvent]:
    """Normalize a Telegram Update into our schema. Text messages only for now.

    Handles both fresh (`message`) and `edited_message` updates; group and
    channel chats normalize the same way as private ones, tagged by chat_type.
    """
    edited = "edited_message" in data
    message = data.get("message") or data.get("edited_message")
    if message is None or message.get("text") is None:
        return []
    chat = message["chat"]
    chat_id = chat["id"]
    sender = message.get("from") or {}
    sender_name = " ".join(
        part for part in (sender.get("first_name"), sender.get("last_name")) if part
    )
    provider_message_id = f"{chat_id}:{message['message_id']}"
    common = {
        "external_event_id": f"{bot_id}:{data['update_id']}",
        "provider_inbox_id": bot_id,
        "provider_message_id": provider_message_id,
        "provider_thread_id": str(chat_id),
        "sender_address": sender.get("username") or str(sender.get("id", "")) or None,
        "sender_name": sender_name or None,
        "chat_type": chat.get("type"),
    }
    match = COMMAND_RE.match(message["text"].strip())
    if match and not edited:
        return [
            InboundCommand(
                **common,
                command=match.group(1),
                text=match.group(2) or "",
            )
        ]
    return [
        InboundMessage(
            **common,
            text=message["text"],
            edited=edited,
        )
    ]


class TelegramProvider:
    name = "telegram"
    channel = "telegram"
    connect_credentials = ("bot_token",)
    # A Bot API bot cannot cold-start (INITIATE), read history (BACKFILL), see
    # presence, or auto-join — those need a user account (see telegram_user).
    # GROUP_VISIBILITY requires privacy mode disabled via @BotFather.
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.GROUP_VISIBILITY,
            Capability.EDIT_INBOUND,
            Capability.COMMANDS,
        }
    )

    def __init__(
        self,
        webhook_base: str = "",
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self._webhook_base = webhook_base.rstrip("/")
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def _call(self, bot_token: str, method: str, body: dict | None = None) -> dict:
        response = self._client.post(f"/bot{bot_token}/{method}", json=body or {})
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    @staticmethod
    def _token(credentials: Mapping[str, str] | None) -> str:
        token = (credentials or {}).get("bot_token", "")
        if not token or ":" not in token:
            raise ValueError("connection is missing a valid bot_token credential")
        return token

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        token = self._token(request.credentials)
        me = self._call(token, "getMe")
        if self._webhook_base:
            body = {
                "url": f"{self._webhook_base}/{me['id']}",
                "allowed_updates": ["message", "edited_message"],
            }
            secret = request.credentials.get("webhook_secret")
            if secret:
                body["secret_token"] = secret
            self._call(token, "setWebhook", body)
        return ProvisionResult(
            address=f"@{me['username']}",
            provider_resource_id=str(me["id"]),
        )

    def typing(self, provider_thread_id: str, credentials: Mapping[str, str] | None = None) -> None:
        """Show the 'typing…' chat action (~5s) while the agent thinks."""
        token = self._token(credentials)
        self._call(token, "sendChatAction",
                   {"chat_id": provider_thread_id, "action": "typing"})

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        token = self._token(credentials)
        chat_id = message.to[0]
        result = self._call(
            token, "sendMessage", {"chat_id": chat_id, "text": message.text or ""}
        )
        return SendResult(
            provider_message_id=f"{chat_id}:{result['message_id']}",
            provider_thread_id=str(chat_id),
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        token = self._token(credentials)
        chat_id, target_message_id = split_composite_id(provider_message_id)
        result = self._call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": message.text or "",
                "reply_to_message_id": int(target_message_id),
                "allow_sending_without_reply": True,
            },
        )
        return SendResult(
            provider_message_id=f"{chat_id}:{result['message_id']}",
            provider_thread_id=chat_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundEvent]:
        if credentials is None:
            # Telegram webhooks are always per-connection; the scoped route
            # supplies the connection's credentials.
            raise WebhookVerificationError("telegram webhooks require a connection scope")
        secret = credentials.get("webhook_secret")
        if secret:
            received = lower_headers(headers).get(SECRET_HEADER) or ""
            if not hmac.compare_digest(received, secret):
                raise WebhookVerificationError("secret token mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_update(data, bot_id_from_token(self._token(credentials)))
