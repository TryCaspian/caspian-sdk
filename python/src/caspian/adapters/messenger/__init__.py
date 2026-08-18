"""Messenger adapter — the only code that knows the Send API exists.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST",
        "url": "https://graph.facebook.com/v21.0/me/messages",
        "json": {...},
        "headers": {"Authorization": f"Bearer {token}"},
        "native": "<label>",
    }))
Unsupported commands → Result.err(AdapterError(...)).

Note: Messenger free-form replies are limited to the standard 24-hour messaging
window; message tags are required beyond it. That policy is a runner concern,
not enforced here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from caspian.core.commands import Command, Post, Reply, SendMedia, Typing
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import (
    Action,
    Attachment,
    Button,
    Event,
    Message,
    ThreadId,
)

GRAPH_BASE = "https://graph.facebook.com/v21.0"
SEND_URL = f"{GRAPH_BASE}/me/messages"

# Messenger attachment kind per Attachment.type.
_MEDIA_TYPES = {
    "photo": "image",
    "file": "file",
    "audio": "audio",
    "voice": "audio",
    "video": "video",
    "sticker": "image",
}


class MessengerAdapter:
    """Adapter for the Facebook Messenger Platform (Send API)."""

    @property
    def name(self) -> str:
        return "messenger"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Verify the Meta X-Hub-Signature-256 payload signature (constant-time)."""
        secret = conn.config.get("app_secret", "")
        if not secret:
            return True
        got = raw.headers.get("X-Hub-Signature-256", "")
        digest = hmac.new(secret.encode(), raw.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest("sha256=" + digest, got)

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Messenger webhook into kernel Events.

        Unknown payload shapes → empty list (parse law). Never raises.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        events: list[Event] = []
        for entry in _as_list(payload.get("entry")):
            for m in _as_list(entry.get("messaging")):
                if isinstance(m, dict):
                    events.extend(self._parse_messaging(m))
        return Result.ok(events)

    def _parse_messaging(self, m: dict[str, Any]) -> list[Event]:
        sender = str(m.get("sender", {}).get("id", ""))
        thread_id = ThreadId(f"messenger:{sender}")

        if "postback" in m:
            pb = m.get("postback", {})
            return [
                Action(
                    thread_id=thread_id,
                    data=str(pb.get("payload", "")),
                    sender=sender,
                    metadata={"title": str(pb.get("title", ""))},
                    raw=m,
                )
            ]

        if "message" in m:
            msg = m.get("message", {})
            reply_to = str(msg.get("reply_to", {}).get("mid", ""))
            return [
                Message(
                    thread_id=thread_id,
                    text=str(msg.get("text", "")),
                    chat_kind="dm",
                    sender=sender,
                    message_id=str(msg.get("mid", "")),
                    attachments=self._extract_attachments(msg),
                    reply_to=reply_to,
                    raw=m,
                )
            ]
        return []

    def _extract_attachments(self, msg: dict[str, Any]) -> tuple[Attachment, ...]:
        kind_map = {
            "image": "photo",
            "file": "file",
            "audio": "audio",
            "video": "video",
        }
        out: list[Attachment] = []
        for att in _as_list(msg.get("attachments")):
            if not isinstance(att, dict):
                continue
            att_type = kind_map.get(att.get("type", ""), "")
            if not att_type:
                continue
            payload = att.get("payload", {})
            url = str(payload.get("url", "")) if isinstance(payload, dict) else ""
            out.append(Attachment(type=att_type, url=url))
        return tuple(out)

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        token = conn.config.get("page_access_token", "")
        if not token:
            return Result.err(
                AdapterError(
                    reason="No page_access_token in connection config",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )

        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                body = self._message_body(tid, text, actions)
                return Result.ok(self._req(token, body, "message"))

            case Reply(thread_id=tid, text=text, actions=actions):
                # Messenger has no native reply-to; ignore reply_to.
                body = self._message_body(tid, text, actions)
                return Result.ok(self._req(token, body, "message"))

            case SendMedia(thread_id=tid, attachment=att, caption=_caption):
                body = {
                    "recipient": {"id": self._psid(tid)},
                    "message": {
                        "attachment": {
                            "type": _MEDIA_TYPES.get(att.type, "file"),
                            "payload": {"url": att.url, "is_reusable": True},
                        }
                    },
                }
                return Result.ok(self._req(token, body, "attachment"))

            case Typing(thread_id=tid):
                body = {
                    "recipient": {"id": self._psid(tid)},
                    "sender_action": "typing_on",
                }
                return Result.ok(self._req(token, body, "typing_on"))

            case _:
                # Edit / Delete / React / Pin: no Send API support.
                return Result.err(
                    AdapterError(
                        reason=f"Messenger does not support {getattr(cmd, 'tag', 'command')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def overlap_key(self, event: Event) -> str:
        return str(event.thread_id)

    def capabilities(self) -> frozenset[str]:
        return frozenset({"receive", "reply", "send", "media", "buttons", "typing"})

    def format(self, text: str) -> str:
        """Messenger renders plain text; nothing to escape."""
        return text

    def encode_thread(self, psid: str) -> ThreadId:
        return ThreadId(f"messenger:{psid}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _psid(self, thread_id: ThreadId) -> str:
        return self.decode_thread(thread_id)

    def _message_body(
        self, tid: ThreadId, text: str, actions: tuple[Button, ...]
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"text": text}
        if actions:
            message["quick_replies"] = [
                {
                    "content_type": "text",
                    "title": b.label,
                    "payload": b.data or b.label,
                }
                for b in actions[:13]
            ]
        return {"recipient": {"id": self._psid(tid)}, "message": message}

    def _req(self, token: str, body: dict[str, Any], native: str) -> Sent:
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": SEND_URL,
                "json": body,
                "headers": {"Authorization": f"Bearer {token}"},
                "native": native,
            }
        )


def _as_list(value: Any) -> list[Any]:  # noqa: ANN401
    return value if isinstance(value, list) else []
