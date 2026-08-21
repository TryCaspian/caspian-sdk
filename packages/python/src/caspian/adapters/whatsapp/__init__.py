"""WhatsApp adapter — the only code that knows the Cloud API exists.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST",
        "url": "https://graph.facebook.com/v21.0/{phone_number_id}/messages",
        "json": {...},
        "headers": {"Authorization": f"Bearer {token}"},
        "native": "<label>",
    }))
Unsupported commands → Result.err(AdapterError(...)).

Note: WhatsApp only permits free-form business-initiated messages inside the
24-hour customer service window; outside it a pre-approved template is required.
This adapter does not enforce that window — it is a runner/policy concern.
"""

from __future__ import annotations

import json
from typing import Any

from caspian.adapters.pack import pack
from caspian.adapters.verify import hmac_hex
from caspian.core.commands import Command, Post, React, Reply, SendMedia
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import (
    Attachment,
    Button,
    Event,
    Message,
    Reaction,
    Receipt,
    ThreadId,
)

GRAPH_BASE = "https://graph.facebook.com/v21.0"

# WhatsApp media object kind per Attachment.type.
_MEDIA_TYPES = {
    "photo": "image",
    "file": "document",
    "audio": "audio",
    "voice": "audio",
    "video": "video",
    "sticker": "sticker",
}


class _WhatsApp:

    def parse(self, raw: RawInbound) -> Result:
        """Parse a WhatsApp webhook into kernel Events.

        Unknown payload shapes → empty list (parse law). Never raises.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        events: list[Event] = []
        for entry in _as_list(payload.get("entry")):
            for change in _as_list(entry.get("changes")):
                value = change.get("value", {}) if isinstance(change, dict) else {}
                for msg in _as_list(value.get("messages")):
                    events.extend(self._parse_message(msg))
                for status in _as_list(value.get("statuses")):
                    events.extend(self._parse_status(status))
        return Result.ok(events)

    def _parse_message(self, msg: dict[str, Any]) -> list[Event]:
        wa_id = str(msg.get("from", ""))
        thread_id = ThreadId(f"whatsapp:{wa_id}")
        message_id = str(msg.get("id", ""))
        msg_type = msg.get("type", "")

        if msg_type == "reaction":
            r = msg.get("reaction", {})
            return [
                Reaction(
                    thread_id=thread_id,
                    emoji=str(r.get("emoji", "")),
                    sender=wa_id,
                    message_id=str(r.get("message_id", "")),
                    raw=msg,
                )
            ]

        attachments = self._extract_attachments(msg, msg_type)
        text = ""
        if msg_type == "text":
            text = str(msg.get("text", {}).get("body", ""))
        return [
            Message(
                thread_id=thread_id,
                text=text,
                chat_kind="dm",
                sender=wa_id,
                message_id=message_id,
                attachments=attachments,
                raw=msg,
            )
        ]

    def _extract_attachments(
        self, msg: dict[str, Any], msg_type: str
    ) -> tuple[Attachment, ...]:
        kind_map = {
            "image": "photo",
            "document": "file",
            "audio": "audio",
            "voice": "voice",
            "video": "video",
            "sticker": "sticker",
        }
        att_type = kind_map.get(msg_type, "")
        if not att_type:
            return ()
        media = msg.get(msg_type, {})
        if not isinstance(media, dict):
            return ()
        return (
            Attachment(
                type=att_type,
                file_id=str(media.get("id", "")),
                filename=str(media.get("filename", "")),
                mime_type=str(media.get("mime_type", "")),
                caption=str(media.get("caption", "")),
            ),
        )

    def _parse_status(self, status: dict[str, Any]) -> list[Event]:
        state = status.get("status", "")
        if state not in ("read", "delivered"):
            return []
        wa_id = str(status.get("recipient_id", ""))
        return [
            Receipt(
                thread_id=ThreadId(f"whatsapp:{wa_id}"),
                status=state,
                sender=wa_id,
                message_id=str(status.get("id", "")),
                raw=status,
            )
        ]

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        token = conn.config.get("access_token", "")
        if not token:
            return Result.err(
                AdapterError(
                    reason="No access_token in connection config",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )
        phone_id = str(conn.config.get("phone_number_id", ""))
        url = f"{GRAPH_BASE}/{phone_id}/messages"

        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                body = self._message_body(tid, text, actions)
                native = "interactive" if actions else "text"
                return Result.ok(self._req(url, token, body, native))

            case Reply(thread_id=tid, reply_to=rid, text=text, actions=actions):
                body = self._message_body(tid, text, actions)
                if rid:
                    body["context"] = {"message_id": rid}
                return Result.ok(self._req(url, token, body, "reply"))

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                return Result.ok(self._media_req(url, token, tid, att, caption))

            case React(thread_id=tid, message_id=mid, emoji=emoji):
                body = {
                    "messaging_product": "whatsapp",
                    "to": self._wa_id(tid),
                    "type": "reaction",
                    "reaction": {"message_id": mid, "emoji": emoji},
                }
                return Result.ok(self._req(url, token, body, "reaction"))

            case _:
                # Edit / Delete / Typing / Pin and friends: no Cloud API support.
                return Result.err(
                    AdapterError(
                        reason=f"WhatsApp does not support {getattr(cmd, 'tag', 'command')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def format(self, text: str) -> str:
        """WhatsApp accepts a light markdown dialect verbatim; no escaping needed."""
        return text

    def encode_thread(self, wa_id: str) -> ThreadId:
        return ThreadId(f"whatsapp:{wa_id}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _wa_id(self, thread_id: ThreadId) -> str:
        return self.decode_thread(thread_id)

    def _message_body(
        self, tid: ThreadId, text: str, actions: tuple[Button, ...]
    ) -> dict[str, Any]:
        to = self._wa_id(tid)
        if actions:
            buttons = [
                {
                    "type": "reply",
                    "reply": {"id": b.data or b.label, "title": b.label},
                }
                for b in actions[:3]
            ]
            return {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": text},
                    "action": {"buttons": buttons},
                },
            }
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

    def _media_req(
        self, url: str, token: str, tid: ThreadId, att: Attachment, caption: str
    ) -> Sent:
        wa_type = _MEDIA_TYPES.get(att.type, "document")
        media: dict[str, Any] = {"link": att.url}
        cap = caption or att.caption
        if cap and wa_type in ("image", "document", "video"):
            media["caption"] = cap
        if att.filename and wa_type == "document":
            media["filename"] = att.filename
        body = {
            "messaging_product": "whatsapp",
            "to": self._wa_id(tid),
            "type": wa_type,
            wa_type: media,
        }
        return self._req(url, token, body, wa_type)

    def _req(
        self, url: str, token: str, body: dict[str, Any], native: str
    ) -> Sent:
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": url,
                "json": body,
                "headers": {"Authorization": f"Bearer {token}"},
                "native": native,
            }
        )


_impl = _WhatsApp()
WhatsAppAdapter = pack(
    channel="whatsapp",
    parse=_impl.parse,
    plan=_impl.execute,
    verify=hmac_hex(
        header="X-Hub-Signature-256", secret_key="app_secret", prefix="sha256="
    ),
    encode_thread=_impl.encode_thread,
    decode_thread=_impl.decode_thread,
)


def _as_list(value: Any) -> list[Any]:  # noqa: ANN401
    return value if isinstance(value, list) else []
