"""Telegram adapter — the only code that knows Bot API exists.

Satisfies adapter laws: ack, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json" | "http_form" | "http_multipart" | "smtp" | "gateway",
        "method": "POST",           # HTTP verb (http_* transports)
        "url": "https://...",       # fully-qualified endpoint
        "json": {...},              # body for http_json
        "headers": {...},           # optional
        "native": "sendMessage",    # platform method name (debug/tests)
    }))
A shared HttpTransport dispatches http_json/http_form/http_multipart payloads.
"""

from __future__ import annotations

import json
from typing import Any

from caspian.adapters.pack import from_response, pack
from caspian.adapters.verify import header_equals
from caspian.core.commands import (
    Call,
    Command,
    Delete,
    Edit,
    Forward,
    Initiate,
    ListHistory,
    MarkRead,
    Pin,
    Post,
    React,
    Reply,
    ScheduleSend,
    SendBlocks,
    SendMedia,
    Typing,
    Unpin,
)
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import (
    Action,
    Attachment,
    Button,
    Edited,
    Event,
    MemberJoin,
    MemberLeave,
    Message,
    Reaction,
    ThreadId,
)

API_BASE = "https://api.telegram.org"

_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"


class _Telegram:

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Telegram Update into kernel Events.

        Unknown update types → empty list (parse law). Never raises.
        """
        try:
            update = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        events: list[Event] = []

        if "message" in update:
            events.extend(self._parse_message(update["message"]))
        elif "edited_message" in update:
            events.extend(self._parse_edited(update["edited_message"]))
        elif "callback_query" in update:
            events.extend(self._parse_callback(update["callback_query"]))
        elif "message_reaction" in update:
            events.extend(self._parse_reaction(update["message_reaction"]))

        return Result.ok(events)

    def _thread_id(self, chat: dict[str, Any], msg: dict[str, Any] | None = None) -> ThreadId:
        chat_id = str(chat.get("id", ""))
        if msg and msg.get("message_thread_id") and msg.get("is_topic_message"):
            return ThreadId(f"telegram:{chat_id}:{msg['message_thread_id']}")
        return ThreadId(f"telegram:{chat_id}")

    def _chat_kind(self, chat: dict[str, Any]) -> str:
        t = chat.get("type", "private")
        if t == "private":
            return "dm"
        if t == "channel":
            return "channel"
        return "group"

    def _parse_message(self, msg: dict[str, Any]) -> list[Event]:
        chat = msg.get("chat", {})
        sender = str(msg.get("from", {}).get("id", ""))
        thread_id = self._thread_id(chat, msg)

        # membership events
        if msg.get("new_chat_members"):
            return [
                MemberJoin(
                    thread_id=thread_id,
                    member=str(m.get("id", "")),
                    raw=msg,
                )
                for m in msg["new_chat_members"]
            ]
        if msg.get("left_chat_member"):
            return [
                MemberLeave(
                    thread_id=thread_id,
                    member=str(msg["left_chat_member"].get("id", "")),
                    raw=msg,
                )
            ]

        attachments = self._extract_attachments(msg)
        reply_to = ""
        if msg.get("reply_to_message"):
            reply_to = str(msg["reply_to_message"].get("message_id", ""))

        topic_id = ""
        if msg.get("is_topic_message") and msg.get("message_thread_id"):
            topic_id = str(msg["message_thread_id"])

        return [
            Message(
                thread_id=thread_id,
                text=msg.get("text", msg.get("caption", "")),
                chat_kind=self._chat_kind(chat),
                sender=sender,
                message_id=str(msg.get("message_id", "")),
                attachments=attachments,
                reply_to=reply_to,
                topic_id=topic_id,
                raw=msg,
            )
        ]

    def _extract_attachments(self, msg: dict[str, Any]) -> tuple[Attachment, ...]:
        out: list[Attachment] = []
        if "photo" in msg and msg["photo"]:
            largest = msg["photo"][-1]
            out.append(
                Attachment(
                    type="photo",
                    file_id=str(largest.get("file_id", "")),
                    size_bytes=int(largest.get("file_size", 0)),
                    caption=msg.get("caption", ""),
                )
            )
        if "document" in msg:
            d = msg["document"]
            out.append(
                Attachment(
                    type="file",
                    file_id=str(d.get("file_id", "")),
                    filename=d.get("file_name", ""),
                    mime_type=d.get("mime_type", ""),
                    size_bytes=int(d.get("file_size", 0)),
                    caption=msg.get("caption", ""),
                )
            )
        if "audio" in msg or "voice" in msg:
            a = msg.get("audio", msg.get("voice", {}))
            out.append(
                Attachment(
                    type="voice" if "voice" in msg else "audio",
                    file_id=str(a.get("file_id", "")),
                    mime_type=a.get("mime_type", ""),
                    size_bytes=int(a.get("file_size", 0)),
                )
            )
        if "video" in msg:
            v = msg["video"]
            out.append(
                Attachment(
                    type="video",
                    file_id=str(v.get("file_id", "")),
                    mime_type=v.get("mime_type", ""),
                    size_bytes=int(v.get("file_size", 0)),
                    caption=msg.get("caption", ""),
                )
            )
        return tuple(out)

    def _parse_edited(self, msg: dict[str, Any]) -> list[Event]:
        chat = msg.get("chat", {})
        return [
            Edited(
                thread_id=self._thread_id(chat, msg),
                message_id=str(msg.get("message_id", "")),
                text=msg.get("text", msg.get("caption", "")),
                sender=str(msg.get("from", {}).get("id", "")),
                raw=msg,
            )
        ]

    def _parse_callback(self, cb: dict[str, Any]) -> list[Event]:
        message = cb.get("message", {})
        chat = message.get("chat", {})
        return [
            Action(
                thread_id=self._thread_id(chat, message),
                data=cb.get("data", ""),
                sender=str(cb.get("from", {}).get("id", "")),
                message_id=str(message.get("message_id", "")),
                interaction_id=str(cb.get("id", "")),
                raw=cb,
            )
        ]

    def _parse_reaction(self, r: dict[str, Any]) -> list[Event]:
        chat = r.get("chat", {})
        new = r.get("new_reaction", [])
        emoji = ""
        if new:
            emoji = new[0].get("emoji", "")
        return [
            Reaction(
                thread_id=self._thread_id(chat),
                emoji=emoji,
                sender=str(r.get("user", {}).get("id", "")),
                message_id=str(r.get("message_id", "")),
                removed=len(new) == 0,
                raw=r,
            )
        ]

    def acknowledge(self, event: Event, conn: Connection) -> Result | None:
        """Ack law: answer callback queries so the client spinner stops."""
        if isinstance(event, Action) and event.interaction_id:
            token = conn.config.get("bot_token", "")
            return Result.ok(
                self._req(
                    token,
                    "answerCallbackQuery",
                    {"callback_query_id": event.interaction_id},
                )
            )
        return None

    def poll(self, offset: int, conn: Connection) -> Result:
        """Build a getUpdates request-description for long-polling (pure).

        The adapter performs no I/O: it returns the request Sent, exactly like
        execute(). A polling transport dispatches it and parses the response
        (see caspian.interpreters.polling.fetch_updates).
        """
        token = conn.config.get("bot_token", "")
        if not token:
            return Result.err(AdapterError(reason="No bot_token in connection config"))
        return Result.ok(self._req(token, "getUpdates", {"offset": offset, "timeout": 0}))

    def webhook(self, conn: Connection) -> Result:
        """Plan setWebhook from ``webhook_url`` / ``webhook_secret`` on the connection."""
        token = conn.config.get("bot_token", "")
        url = str(conn.config.get("webhook_url", "") or "")
        if not token:
            return Result.err(AdapterError(reason="No bot_token in connection config"))
        if not url:
            return Result.err(AdapterError(reason="No webhook_url in connection config"))
        body: dict[str, Any] = {"url": url}
        secret = str(conn.config.get("webhook_secret", "") or "")
        if secret:
            body["secret_token"] = secret
        return Result.ok(self._req(token, "setWebhook", body))

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        token = conn.config.get("bot_token", "")
        if not token:
            return Result.err(AdapterError(reason="No bot_token in connection config"))

        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                body = self._msg_body(tid, text, actions)
                return Result.ok(self._req(token, "sendMessage", body))

            case Reply(thread_id=tid, reply_to=rid, text=text, actions=actions):
                body = self._msg_body(tid, text, actions)
                if rid.isdigit():
                    body["reply_parameters"] = {"message_id": int(rid)}
                return Result.ok(self._req(token, "sendMessage", body))

            case SendBlocks(thread_id=tid, blocks=blocks, text=text, actions=actions):
                # Telegram has no native blocks; render to text + keyboard.
                rendered = text or self._blocks_to_text(blocks)
                body = self._msg_body(tid, rendered, actions)
                return Result.ok(self._req(token, "sendMessage", body))

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                return Result.ok(self._media_req(token, tid, att, caption))

            case Edit(thread_id=tid, message_id=mid, text=text, actions=actions):
                body = {
                    "chat_id": self._chat_of(tid),
                    "message_id": self._id(mid),
                    "text": text,
                }
                if actions:
                    body["reply_markup"] = self._keyboard(actions)
                return Result.ok(self._req(token, "editMessageText", body))

            case Delete(thread_id=tid, message_id=mid):
                body = {"chat_id": self._chat_of(tid), "message_id": self._id(mid)}
                return Result.ok(self._req(token, "deleteMessage", body))

            case React(thread_id=tid, message_id=mid, emoji=emoji):
                body = {
                    "chat_id": self._chat_of(tid),
                    "message_id": self._id(mid),
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                }
                return Result.ok(self._req(token, "setMessageReaction", body))

            case Typing(thread_id=tid):
                body = {"chat_id": self._chat_of(tid), "action": "typing"}
                return Result.ok(self._req(token, "sendChatAction", body))

            case Pin(thread_id=tid, message_id=mid):
                body = {"chat_id": self._chat_of(tid), "message_id": self._id(mid)}
                return Result.ok(self._req(token, "pinChatMessage", body))

            case Unpin(thread_id=tid, message_id=mid):
                body = {"chat_id": self._chat_of(tid), "message_id": self._id(mid)}
                return Result.ok(self._req(token, "unpinChatMessage", body))

            case Forward(from_thread_id=src, to_thread_id=dst, message_id=mid):
                body = {
                    "from_chat_id": self._chat_of(src),
                    "chat_id": self._chat_of(dst),
                    "message_id": self._id(mid),
                }
                return Result.ok(self._req(token, "forwardMessage", body))

            case MarkRead():
                # Telegram Bot API has no read-marking; no-op success.
                return Result.ok(Sent(raw={"transport": "noop", "native": "markRead"}))

            case Initiate(thread_id=tid, text=text, actions=actions):
                # Bots can only message users who started them; same as sendMessage.
                body = self._msg_body(tid, text, actions)
                return Result.ok(self._req(token, "sendMessage", body))

            case ScheduleSend():
                # Telegram Bot API cannot schedule; the runner/outbox must hold it.
                return Result.err(
                    AdapterError(
                        reason="Telegram cannot schedule server-side; use the runner's scheduler",
                        command_tag="ScheduleSend",
                    )
                )

            case ListHistory():
                return Result.err(
                    AdapterError(
                        reason="Telegram Bot API cannot backfill history (needs MTProto)",
                        command_tag="ListHistory",
                    )
                )

            case Call(method=method, args=args):
                return Result.ok(self._req(token, method, dict(args)))

            case _:
                return Result.err(
                    AdapterError(
                        reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def format(self, text: str) -> str:
        """Escape text for Telegram MarkdownV2."""
        out = []
        for ch in text:
            if ch in _MDV2_SPECIAL:
                out.append("\\" + ch)
            else:
                out.append(ch)
        return "".join(out)

    def encode_thread(self, chat_id: str, topic_id: str = "") -> ThreadId:
        if topic_id:
            return ThreadId(f"telegram:{chat_id}:{topic_id}")
        return ThreadId(f"telegram:{chat_id}")

    def decode_thread(self, thread_id: ThreadId) -> tuple[str, str]:
        parts = str(thread_id).split(":")
        chat_id = parts[1] if len(parts) > 1 else ""
        topic_id = parts[2] if len(parts) > 2 else ""
        return chat_id, topic_id

    # ─── Internal ────────────────────────────────────────────────────────────

    def _chat_of(self, thread_id: ThreadId) -> str:
        return self.decode_thread(thread_id)[0]

    def _id(self, message_id: str) -> int | str:
        return int(message_id) if message_id.isdigit() else message_id

    def _msg_body(
        self, tid: ThreadId, text: str, actions: tuple[Button, ...]
    ) -> dict[str, Any]:
        chat_id, topic_id = self.decode_thread(tid)
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if topic_id:
            body["message_thread_id"] = int(topic_id) if topic_id.isdigit() else topic_id
        if actions:
            body["reply_markup"] = self._keyboard(actions)
        return body

    def _media_req(
        self, token: str, tid: ThreadId, att: Attachment, caption: str
    ) -> Sent:
        method_map = {
            "photo": ("sendPhoto", "photo"),
            "file": ("sendDocument", "document"),
            "audio": ("sendAudio", "audio"),
            "voice": ("sendVoice", "voice"),
            "video": ("sendVideo", "video"),
            "sticker": ("sendSticker", "sticker"),
        }
        method, field = method_map.get(att.type, ("sendDocument", "document"))
        chat_id = self._chat_of(tid)
        body: dict[str, Any] = {"chat_id": chat_id, field: att.url or att.file_id}
        if caption or att.caption:
            body["caption"] = caption or att.caption
        return self._req(token, method, body)

    def _keyboard(self, actions: tuple[Button, ...]) -> dict[str, Any]:
        row = []
        for b in actions:
            btn: dict[str, Any] = {"text": b.label}
            if b.url:
                btn["url"] = b.url
            else:
                btn["callback_data"] = b.data
            row.append(btn)
        return {"inline_keyboard": [row]}

    def _blocks_to_text(self, blocks: tuple[Any, ...]) -> str:
        lines: list[str] = []
        for b in blocks:
            content = getattr(b, "content", {})
            if content.get("text"):
                lines.append(str(content["text"]))
        return "\n".join(lines)

    def _req(self, token: str, method: str, body: dict[str, Any]) -> Sent:
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": f"{API_BASE}/bot{token}/{method}",
                "json": body,
                "native": method,
            }
        )


_impl = _Telegram()
TelegramAdapter = pack(
    channel="telegram",
    parse=_impl.parse,
    plan=_impl.execute,
    verify=header_equals(
        header="X-Telegram-Bot-Api-Secret-Token", secret_key="webhook_secret"
    ),
    encode_thread=_impl.encode_thread,
    decode_thread=_impl.decode_thread,
    acknowledge=_impl.acknowledge,
    poll=_impl.poll,
    webhook=_impl.webhook,
    posted_id=from_response("result", "message_id"),
)
