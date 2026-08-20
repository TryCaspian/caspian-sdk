"""Twilio SMS/RCS adapter — the only code that knows the Twilio Messages API exists.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Inbound Twilio webhooks are form-encoded (application/x-www-form-urlencoded), so
parse decodes raw.body with urllib.parse.parse_qs. Outbound uses the form-encoded
Messages API, dispatched via the shared "http_form" transport.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_form",
        "method": "POST",
        "url": "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        "form": {...},
        "headers": {"Authorization": "Basic ..."},
        "native": "<label>",
    }))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from caspian.core.commands import Command, Post, Reply, SendMedia
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import Attachment, Event, Message, ThreadId

API_BASE = "https://api.twilio.com/2010-04-01"


class SmsAdapter:
    """Adapter for Twilio SMS/RCS (Messages API)."""

    @property
    def name(self) -> str:
        return "sms"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Verify the Twilio X-Twilio-Signature header (best-effort).

        HMAC-SHA1 of url + sorted(concat key+value) params, base64-encoded.
        If auth_token or webhook_url are not configured → return True.
        """
        auth_token = conn.config.get("auth_token", "")
        webhook_url = conn.config.get("webhook_url", "")
        if not auth_token or not webhook_url:
            return True

        signature = raw.headers.get("X-Twilio-Signature", "")
        try:
            params = parse_qs(raw.body.decode())
        except (UnicodeDecodeError, ValueError):
            return False

        payload = webhook_url
        for key in sorted(params):
            for value in params[key]:
                payload += key + value

        digest = hmac.new(
            auth_token.encode(), payload.encode(), hashlib.sha1
        ).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, signature)

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Twilio inbound message webhook into kernel Events.

        Unknown/empty forms → empty list (parse law). Undecodable → DecodeError.
        """
        try:
            form = parse_qs(raw.body.decode())
        except (UnicodeDecodeError, ValueError) as e:
            return Result.err(DecodeError(reason=f"Invalid form body: {e}"))

        def first(key: str) -> str:
            values = form.get(key)
            return values[0] if values else ""

        from_number = first("From")
        if not from_number:
            return Result.ok([])

        attachments = self._extract_attachments(form, first)

        message = Message(
            thread_id=ThreadId(f"sms:{from_number}"),
            text=first("Body"),
            chat_kind="dm",
            sender=from_number,
            message_id=first("MessageSid"),
            attachments=attachments,
            raw={k: v for k, v in form.items()},
        )
        return Result.ok([message])

    def _extract_attachments(
        self, form: dict[str, Any], first: Callable[[str], str]
    ) -> tuple[Attachment, ...]:
        try:
            num_media = int(first("NumMedia") or "0")
        except ValueError:
            num_media = 0

        out: list[Attachment] = []
        for i in range(num_media):
            url = first(f"MediaUrl{i}")
            if not url:
                continue
            out.append(
                Attachment(
                    type="file",
                    url=url,
                    mime_type=first(f"MediaContentType{i}"),
                )
            )
        return tuple(out)

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        sid = conn.config.get("account_sid", "")
        token = conn.config.get("auth_token", "")
        if not sid or not token:
            return Result.err(
                AdapterError(
                    reason="No account_sid/auth_token in connection config",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )
        from_number = conn.config.get("from_number", "")

        match cmd:
            case Post(thread_id=tid, text=text):
                form = self._msg_form(tid, from_number, text)
                return Result.ok(self._req(sid, token, form, "sendMessage"))

            case Reply(thread_id=tid, text=text):
                # SMS has no reply-to; a reply is just another message.
                form = self._msg_form(tid, from_number, text)
                return Result.ok(self._req(sid, token, form, "sendMessage"))

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                form = self._msg_form(tid, from_number, caption)
                form["MediaUrl"] = att.url or att.file_id
                return Result.ok(self._req(sid, token, form, "sendMedia"))

            case _:
                return Result.err(
                    AdapterError(
                        reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def overlap_key(self, event: Event) -> str:
        return str(event.thread_id)

    def capabilities(self) -> frozenset[str]:
        return frozenset({"receive", "reply", "send", "media"})

    def format(self, text: str) -> str:
        """SMS is plain text; no formatting transformation needed."""
        return text

    def encode_thread(self, number: str) -> ThreadId:
        return ThreadId(f"sms:{number}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _msg_form(self, tid: ThreadId, from_number: str, text: str) -> dict[str, str]:
        return {
            "To": self.decode_thread(tid),
            "From": from_number,
            "Body": text,
        }

    def _basic_auth(self, sid: str, token: str) -> str:
        raw = f"{sid}:{token}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _req(
        self, sid: str, token: str, form: dict[str, str], native: str
    ) -> Sent:
        return Sent(
            raw={
                "transport": "http_form",
                "method": "POST",
                "url": f"{API_BASE}/Accounts/{sid}/Messages.json",
                "form": form,
                "headers": {"Authorization": self._basic_auth(sid, token)},
                "native": native,
            }
        )
