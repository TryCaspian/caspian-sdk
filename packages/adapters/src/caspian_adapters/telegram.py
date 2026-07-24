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
from collections.abc import Mapping

import httpx

from .base import (
    Attachment,
    Capability,
    InboundCommand,
    InboundEvent,
    InboundMessage,
    InboundReaction,
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


def parse_attachments(message: dict) -> list[Attachment]:
    """Pull any file attachments out of a Telegram message.

    Telegram returns an opaque ``file_id`` rather than a URL, so we keep it in
    ``provider_file_id`` (the actual download link needs a ``getFile`` call
    resolved downstream) alongside whatever metadata the update carries. Photos
    arrive as a list of rescaled sizes; the last is the largest.
    """
    out: list[Attachment] = []
    photos = message.get("photo")
    if photos:
        largest = photos[-1]
        out.append(
            Attachment(
                mime_type="image/jpeg",
                size_bytes=largest.get("file_size"),
                provider_file_id=largest.get("file_id"),
            )
        )
    document = message.get("document")
    if document:
        out.append(
            Attachment(
                mime_type=document.get("mime_type"),
                filename=document.get("file_name"),
                size_bytes=document.get("file_size"),
                provider_file_id=document.get("file_id"),
            )
        )
    voice = message.get("voice")
    if voice:
        out.append(
            Attachment(
                mime_type=voice.get("mime_type"),
                size_bytes=voice.get("file_size"),
                provider_file_id=voice.get("file_id"),
            )
        )
    return out


def parse_update(data: dict, bot_id: str) -> list[InboundEvent]:
    """Normalize a Telegram Update into our schema.

    Handles text and media messages, bot commands detected via entities,
    edited messages, and message_reaction updates. Returns a list of
    InboundEvent (InboundMessage | InboundReaction | InboundCommand).
    """
    # --- Reaction events ---
    reaction_update = data.get("message_reaction") or data.get("message_reaction_updated")
    if reaction_update is not None:
        chat = reaction_update.get("chat", {})
        chat_id = chat.get("id", "")
        old_reaction = reaction_update.get("old_reaction", [])
        new_reaction = reaction_update.get("new_reaction", [])
        user = reaction_update.get("user") or reaction_update.get("actor_chat") or {}
        sender_address = user.get("username") or str(user.get("id", "")) or None
        # Determine which emoji was added or removed by diffing old/new
        old_emojis = {r.get("emoji", "") for r in old_reaction}
        new_emojis = {r.get("emoji", "") for r in new_reaction}
        added = new_emojis - old_emojis
        removed = old_emojis - new_emojis
        results: list[InboundEvent] = []
        for emoji in added:
            results.append(
                InboundReaction(
                    external_event_id=f"{bot_id}:{data['update_id']}:add:{emoji}",
                    provider_inbox_id=bot_id,
                    emoji=emoji,
                    action="added",
                    source_provider_message_id=f"{chat_id}:{reaction_update.get('message_id', '')}",
                    sender_address=sender_address,
                )
            )
        for emoji in removed:
            results.append(
                InboundReaction(
                    external_event_id=f"{bot_id}:{data['update_id']}:rm:{emoji}",
                    provider_inbox_id=bot_id,
                    emoji=emoji,
                    action="removed",
                    source_provider_message_id=f"{chat_id}:{reaction_update.get('message_id', '')}",
                    sender_address=sender_address,
                )
            )
        return results

    # --- Text messages (including bot commands) ---
    edited = "edited_message" in data
    message = data.get("message") or data.get("edited_message")
    if message is None:
        return []
    attachments = parse_attachments(message)
    text = message.get("text") or message.get("caption")
    if text is None and not attachments:
        return []
    chat = message["chat"]
    chat_id = chat["id"]
    sender = message.get("from") or {}
    sender_name = " ".join(
        part for part in (sender.get("first_name"), sender.get("last_name")) if part
    )
    sender_address = sender.get("username") or str(sender.get("id", "")) or None

    # Check for bot command entity at position 0.
    entities = message.get("entities", [])
    for entity in entities:
        if entity.get("type") == "bot_command" and entity.get("offset", 0) == 0:
            command_text = text[:entity.get("length", len(text))]
            args = text[entity.get("length", len(text)):].strip() or None
            return [
                InboundCommand(
                    external_event_id=f"{bot_id}:{data['update_id']}",
                    provider_inbox_id=bot_id,
                    provider_message_id=f"{chat_id}:{message['message_id']}",
                    provider_thread_id=str(chat_id),
                    command=command_text,
                    args=args,
                    text=text,
                    sender_address=sender_address,
                    sender_name=sender_name or None,
                    chat_type=chat.get("type"),
                )
            ]

    return [
        InboundMessage(
            external_event_id=f"{bot_id}:{data['update_id']}",
            provider_inbox_id=bot_id,
            provider_message_id=f"{chat_id}:{message['message_id']}",
            provider_thread_id=str(chat_id),
            sender_address=sender_address,
            sender_name=sender_name or None,
            text=text,
            chat_type=chat.get("type"),
            edited=edited,
            attachments=attachments,
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
            Capability.ATTACHMENTS,
            Capability.REACTIONS,
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

    @staticmethod
    def _media_method(attachment: Attachment) -> tuple[str, str]:
        """The Telegram send method + body key for an attachment (photo vs file)."""
        if (attachment.mime_type or "").startswith("image/"):
            return "sendPhoto", "photo"
        return "sendDocument", "document"

    def _send_attachment(
        self, token: str, chat_id: str, message: OutboundMessage, reply_to: str | None
    ) -> dict:
        """Send the first attachment with the message text as its caption.

        Telegram accepts a public URL or a previously seen file_id in place of a
        multipart upload, so we pass whichever the attachment carries. Extra
        attachments beyond the first are left to a follow-up (media groups).
        """
        attachment = message.attachments[0]
        media_ref = attachment.url or attachment.provider_file_id
        if not media_ref:
            raise ValueError("attachment needs a url or provider_file_id to send")
        method, key = self._media_method(attachment)
        body: dict = {"chat_id": chat_id, key: media_ref}
        if message.text:
            body["caption"] = message.text
        if reply_to is not None:
            body["reply_to_message_id"] = int(reply_to)
            body["allow_sending_without_reply"] = True
        return self._call(token, method, body)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        token = self._token(request.credentials)
        me = self._call(token, "getMe")
        if self._webhook_base:
            body = {
                "url": f"{self._webhook_base}/{me['id']}",
                "allowed_updates": ["message", "edited_message", "message_reaction"],
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

    def react(
        self, provider_inbox_id: str, provider_message_id: str, emoji: str,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        """Add an emoji reaction to a message (Telegram Bot API setMessageReaction)."""
        token = self._token(credentials)
        chat_id, message_id = split_composite_id(provider_message_id)
        self._call(
            token, "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        token = self._token(credentials)
        chat_id = message.to[0]
        if message.attachments:
            result = self._send_attachment(token, chat_id, message, None)
        else:
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
        if message.attachments:
            result = self._send_attachment(token, chat_id, message, target_message_id)
        else:
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
