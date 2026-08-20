"""Twilio Voice adapter — the only code that knows Twilio Programmable Voice exists.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Inbound Twilio voice webhooks are form-encoded (application/x-www-form-urlencoded),
so parse decodes raw.body with urllib.parse.parse_qs. Outbound TTS is expressed as
TwiML via the "twiml" transport (the shared HttpTransport does not dispatch twiml —
that is a documented follow-up).

Uniform execute() contract (twiml form):
    Result.ok(Sent(raw={
        "transport": "twiml",
        "twiml": '<?xml version="1.0" ...><Response><Say>...</Say></Response>',
        "native": "<label>",
    }))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

from caspian.core.commands import Command, Post, Reply
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import Event, Message, ThreadId


class VoiceAdapter:
    """Adapter for Twilio Programmable Voice (TwiML)."""

    @property
    def name(self) -> str:
        return "voice"

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
        """Parse a Twilio voice webhook into kernel Events.

        No CallSid → empty list (parse law). Undecodable → DecodeError.
        """
        try:
            form = parse_qs(raw.body.decode())
        except (UnicodeDecodeError, ValueError) as e:
            return Result.err(DecodeError(reason=f"Invalid form body: {e}"))

        def first(key: str) -> str:
            values = form.get(key)
            return values[0] if values else ""

        call_sid = first("CallSid")
        if not call_sid:
            return Result.ok([])

        text = first("SpeechResult") or first("TranscriptionText") or ""

        message = Message(
            thread_id=ThreadId(f"voice:{call_sid}"),
            text=text,
            chat_kind="dm",
            sender=first("From"),
            message_id=call_sid,
            raw={k: v for k, v in form.items()},
        )
        return Result.ok([message])

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        match cmd:
            case Post(text=text):
                return Result.ok(self._say(text))

            case Reply(text=text):
                return Result.ok(self._say(text))

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
        return frozenset({"receive", "send", "voice", "tts"})

    def format(self, text: str) -> str:
        """Escape text for inclusion in TwiML (XML)."""
        return escape(text)

    def encode_thread(self, call_sid: str) -> ThreadId:
        return ThreadId(f"voice:{call_sid}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _say(self, text: str) -> Sent:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Say>{self.format(text)}</Say></Response>"
        )
        return Sent(raw={"transport": "twiml", "twiml": twiml, "native": "say"})
