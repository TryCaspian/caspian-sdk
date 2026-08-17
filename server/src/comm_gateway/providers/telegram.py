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

from . import blocks as blocks_render
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

SECRET_HEADER = "x-telegram-bot-api-secret-token"

# Updates we ask Telegram to deliver. message_reaction needs to be requested
# explicitly (it is not in the default set) so tapbacks reach us.
ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "message_reaction"]

# Callback data we render on our buttons is prefixed so we can tell our own taps
# apart (see blocks.to_telegram). Decoding strips it back to the agent's value.
CALLBACK_PREFIX = "caspian:"


def bot_id_from_token(token: str) -> str:
    return token.split(":", 1)[0]


def _decode_callback(data: str | None) -> str:
    value = data or ""
    return value[len(CALLBACK_PREFIX):] if value.startswith(CALLBACK_PREFIX) else value


def _sender_name(sender: dict) -> str | None:
    return " ".join(
        part for part in (sender.get("first_name"), sender.get("last_name")) if part
    ) or None


def _media_from_message(message: dict) -> list[dict]:
    """Extract attachment references from a Telegram message. Each entry carries a
    file_id (resolved to a download URL later via getFile) plus what we know now."""
    media: list[dict] = []
    if message.get("photo"):
        largest = max(message["photo"], key=lambda p: p.get("file_size", 0))
        media.append({"file_id": largest["file_id"], "mime_type": "image/jpeg",
                      "size": largest.get("file_size")})
    doc = message.get("document")
    if doc:
        media.append({"file_id": doc["file_id"], "name": doc.get("file_name"),
                      "mime_type": doc.get("mime_type"), "size": doc.get("file_size")})
    for key, mime in (("voice", "audio/ogg"), ("audio", "audio/mpeg"), ("video", "video/mp4")):
        item = message.get(key)
        if item:
            media.append({"file_id": item["file_id"],
                          "mime_type": item.get("mime_type") or mime,
                          "size": item.get("file_size")})
    return media


def parse_update(data: dict, bot_id: str) -> list[InboundMessage]:
    """Normalize a Telegram Update into our schema.

    Handles text and media messages (fresh + `edited_message`), `callback_query`
    button taps (as interactions), and `message_reaction` tapbacks (as reactions).
    Group and channel chats normalize the same way as private ones, by chat_type.
    """
    # Button tap: the same webhook delivers it. Decode our callback tag and route
    # it as an interaction against the message the button was attached to.
    if "callback_query" in data:
        cbq = data["callback_query"]
        sender = cbq.get("from") or {}
        source = cbq.get("message") or {}
        chat_id = (source.get("chat") or {}).get("id")
        source_id = f"{chat_id}:{source.get('message_id')}" if chat_id is not None else None
        return [
            InboundMessage(
                kind="interaction",
                external_event_id=f"{bot_id}:cbq:{cbq['id']}",
                provider_inbox_id=bot_id,
                provider_message_id=source_id or f"{bot_id}:cbq:{cbq['id']}",
                provider_thread_id=str(chat_id) if chat_id is not None else "",
                sender_address=sender.get("username") or str(sender.get("id", "")) or None,
                sender_name=_sender_name(sender),
                action={"value": _decode_callback(cbq.get("data")),
                        "source_message_id": source_id},
            )
        ]

    # Emoji tapback on a message.
    if "message_reaction" in data:
        mr = data["message_reaction"]
        chat_id = (mr.get("chat") or {}).get("id")
        sender = mr.get("user") or {}
        new = mr.get("new_reaction") or []
        old = mr.get("old_reaction") or []
        added = bool(new)
        picked = (new or old)
        emoji = picked[0].get("emoji") if picked else None
        return [
            InboundMessage(
                kind="reaction",
                external_event_id=f"{bot_id}:react:{chat_id}:{mr.get('message_id')}:{mr.get('date')}",
                provider_inbox_id=bot_id,
                provider_message_id=f"{chat_id}:{mr.get('message_id')}",
                provider_thread_id=str(chat_id) if chat_id is not None else "",
                sender_address=sender.get("username") or str(sender.get("id", "")) or None,
                sender_name=_sender_name(sender),
                reaction={"emoji": emoji, "action": "added" if added else "removed",
                          "source_message_id": f"{chat_id}:{mr.get('message_id')}"},
            )
        ]

    edited = "edited_message" in data
    message = data.get("message") or data.get("edited_message")
    if message is None:
        return []
    media = _media_from_message(message)
    text = message.get("text")
    if text is None:
        text = message.get("caption")  # media messages carry their text as a caption
    if text is None and not media:
        return []  # a message we can't represent (sticker, location, ...)
    chat = message["chat"]
    chat_id = chat["id"]
    sender = message.get("from") or {}

    entities = message.get("entities") or message.get("caption_entities") or []
    command_entity = next((e for e in entities if e.get("type") == "bot_command"), None)

    is_command = False
    cmd_name = ""
    args = None

    if command_entity and command_entity.get("offset") == 0:
        offset = command_entity["offset"]
        length = command_entity["length"]
        if text:
            raw_cmd = text[offset:offset+length]
            cmd_name = raw_cmd.lstrip("/").split("@")[0]
            args = text[offset+length:].strip()
            is_command = True
    elif text and text.startswith("/"):
        match = re.match(r"^/([a-zA-Z0-9_]+)(?:@\w+)?(?:\s+(.*))?$", text)
        if match:
            cmd_name = match.group(1)
            args = match.group(2).strip() if match.group(2) else None
            is_command = True

    if is_command:
        return [
            InboundMessage(
                kind="command",
                external_event_id=f"{bot_id}:{data['update_id']}",
                provider_inbox_id=bot_id,
                provider_message_id=f"{chat_id}:{message['message_id']}",
                provider_thread_id=str(chat_id),
                sender_address=sender.get("username") or str(sender.get("id", "")) or None,
                sender_name=_sender_name(sender),
                command={
                    "command": cmd_name,
                    "text": args if args else None,
                    "source_message_id": f"{chat_id}:{message['message_id']}",
                },
                media=media,
                chat_type=chat.get("type"),
                edited=edited,
            )
        ]

    return [
        InboundMessage(
            external_event_id=f"{bot_id}:{data['update_id']}",
            provider_inbox_id=bot_id,
            provider_message_id=f"{chat_id}:{message['message_id']}",
            provider_thread_id=str(chat_id),
            sender_address=sender.get("username") or str(sender.get("id", "")) or None,
            sender_name=_sender_name(sender),
            text=text,
            media=media,
            chat_type=chat.get("type"),
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
            Capability.INTERACTIONS,
            Capability.MEDIA,
            Capability.REACTIONS,
            Capability.EDIT_OUTBOUND,
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
                "allowed_updates": ALLOWED_UPDATES,
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

    def edit_message(
        self,
        provider_message_id: str,
        text: str,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        token = self._token(credentials)
        chat_id, message_id = split_composite_id(provider_message_id)
        self._call(token, "editMessageText",
                   {"chat_id": chat_id, "message_id": int(message_id), "text": text})

    def react(
        self, provider_message_id: str, emoji: str,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        """Add an emoji reaction to a message (setMessageReaction)."""
        token = self._token(credentials)
        chat_id, message_id = split_composite_id(provider_message_id)
        self._call(token, "setMessageReaction", {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "reaction": [{"type": "emoji", "emoji": emoji}],
        })

    def _send_media(
        self, token: str, chat_id: str, media: tuple, caption: str | None,
        reply_to: int | None = None,
    ) -> dict:
        """Send each attachment as a photo (image/*) or document. Telegram fetches
        the file from its `url`; the first item carries the caption. Returns the
        last sendPhoto/sendDocument result so we can thread on its message id."""
        last: dict = {}
        for index, item in enumerate(media):
            url = item.get("url") or item.get("data")
            if not url:
                continue
            mime = str(item.get("mime_type") or "")
            method = "sendPhoto" if mime.startswith("image/") else "sendDocument"
            field = "photo" if method == "sendPhoto" else "document"
            body: dict = {"chat_id": chat_id, field: url}
            if index == 0 and caption:
                body["caption"] = caption
            if reply_to is not None:
                body["reply_to_message_id"] = reply_to
                body["allow_sending_without_reply"] = True
            last = self._call(token, method, body)
        return last

    def _message_body(self, message: OutboundMessage) -> dict:
        """Base sendMessage body — HTML + inline keyboard when blocks are present,
        plain text otherwise."""
        if message.blocks:
            html_text, markup = blocks_render.to_telegram(message.blocks)
            body: dict = {"text": html_text or (message.text or ""), "parse_mode": "HTML"}
            if markup:
                body["reply_markup"] = markup
            return body
        return {"text": message.text or ""}

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        token = self._token(credentials)
        chat_id = message.to[0]
        if message.media:
            # A media message: the text rides as the caption; no separate sendMessage.
            result = self._send_media(token, chat_id, message.media, message.text)
        else:
            result = self._call(
                token, "sendMessage", {"chat_id": chat_id, **self._message_body(message)}
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
        if message.media:
            result = self._send_media(
                token, chat_id, message.media, message.text, reply_to=int(target_message_id)
            )
        else:
            result = self._call(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    **self._message_body(message),
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
    ) -> list[InboundMessage]:
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
        token = self._token(credentials)
        inbound = parse_update(data, bot_id_from_token(token))
        # Answer a button tap so the client stops its loading spinner. Best-effort:
        # a failed ack must not drop the interaction we already parsed.
        if "callback_query" in data:
            try:
                self._call(token, "answerCallbackQuery",
                           {"callback_query_id": data["callback_query"]["id"]})
            except Exception:
                pass
        # Resolve inbound media file_ids to download URLs (getFile). Best-effort;
        # a message keeps its file_id reference if the resolve fails.
        for item in inbound:
            for att in item.media or []:
                if att.get("url") or not att.get("file_id"):
                    continue
                try:
                    info = self._call(token, "getFile", {"file_id": att["file_id"]})
                    path = info.get("file_path")
                    if path:
                        att["url"] = f"{self._client.base_url}file/bot{token}/{path}"
                except Exception:
                    pass
        return inbound
