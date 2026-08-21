"""Email adapter — the only code that knows SMTP/SES/SNS exists.

Satisfies adapter laws: parse unknown → ok([]); invalid input → DecodeError.
Never raises across the core boundary; never makes routing decisions.

Uniform execute() contract (shared by all adapters). Email uses the "smtp"
transport, which the shared HttpTransport does NOT dispatch — a future
SmtpTransport is a documented follow-up. This adapter only builds the pure
request-description:

    Result.ok(Sent(raw={
        "transport": "smtp",
        "native": "sendmail",
        "email": {
            "from": conn.config.get("from_address", ""),
            "to": <recipient>,
            "subject": <subject>,
            "body": <text>,
            "in_reply_to": <message-id or "">,
            "references": <str>,
            "attachments": [{"filename", "url", "mime_type"}, ...],
        },
    }))

Inbound accepts EITHER an AWS SES→SNS notification (best-effort) or a
simplified {"from","to","subject","body","message_id","in_reply_to"} JSON.
"""

from __future__ import annotations

import email
import json
from email.utils import parseaddr
from typing import Any

from caspian.catalog import capabilities_of
from caspian.core.commands import Command, Post, Reply, SendBlocks, SendMedia
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import Block, Event, Message, ThreadId


class EmailAdapter:
    """Adapter for inbound email (SES/SNS) and outbound SMTP request-descriptions."""

    @property
    def name(self) -> str:
        return "email"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Verify the inbound webhook.

        SNS signature verification is a documented follow-up; accept for now.
        """
        return True

    def parse(self, raw: RawInbound) -> Result:
        """Parse an inbound email payload into kernel Events.

        Unknown payloads → empty list (parse law). Invalid input → DecodeError.
        Never raises.
        """
        try:
            data = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        if not isinstance(data, dict):
            return Result.err(DecodeError(reason="Expected a JSON object"))

        fields: dict[str, str] | None
        try:
            if data.get("Type") == "Notification" and "Message" in data:
                fields = self._from_sns(data)
            else:
                fields = self._from_simple(data)
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
            return Result.err(DecodeError(reason=f"Invalid email payload: {e}"))

        if fields is None:
            return Result.ok([])
        return Result.ok([self._build_message(fields, data)])

    def _build_message(self, fields: dict[str, str], raw: dict[str, Any]) -> Message:
        return Message(
            thread_id=self.encode_thread(fields["sender"]),
            text=fields["body"],
            chat_kind="dm",
            sender=fields["sender"],
            message_id=fields["message_id"],
            reply_to=fields["in_reply_to"],
            raw=raw,
        )

    def _from_simple(self, data: dict[str, Any]) -> dict[str, str] | None:
        keys = ("from", "to", "subject", "body", "message_id")
        if not any(k in data for k in keys):
            return None
        return self._normalize(
            from_raw=str(data.get("from", "")),
            to_raw=str(data.get("to", "")),
            subject=str(data.get("subject", "")),
            body=str(data.get("body", "")),
            message_id=str(data.get("message_id", "")),
            in_reply_to=str(data.get("in_reply_to", "")),
            references=str(data.get("references", "")),
        )

    def _from_sns(self, data: dict[str, Any]) -> dict[str, str]:
        inner = json.loads(data["Message"])
        mail = inner.get("mail", {}) or {}
        headers = mail.get("commonHeaders", {}) or {}
        content = inner.get("content", "") or ""
        mime = self._parse_mime(content) if content else {}
        from_raw = (
            self._first(headers.get("from")) or mail.get("source", "") or mime.get("from", "")
        )
        to_raw = (
            self._first(headers.get("to"))
            or self._first(mail.get("destination"))
            or mime.get("to", "")
        )
        return self._normalize(
            from_raw=from_raw,
            to_raw=to_raw,
            subject=headers.get("subject", "") or mime.get("subject", ""),
            body=mime.get("body", ""),
            message_id=(
                headers.get("messageId", "")
                or mail.get("messageId", "")
                or mime.get("message_id", "")
            ),
            in_reply_to=mime.get("in_reply_to", ""),
            references=mime.get("references", ""),
        )

    def _normalize(
        self,
        *,
        from_raw: str,
        to_raw: str,
        subject: str,
        body: str,
        message_id: str,
        in_reply_to: str,
        references: str,
    ) -> dict[str, str]:
        return {
            "sender": parseaddr(from_raw)[1].lower(),
            "to": parseaddr(to_raw)[1].lower(),
            "subject": subject,
            "body": body,
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
        }

    def _parse_mime(self, content: str) -> dict[str, str]:
        msg = email.message_from_string(content)
        return {
            "from": msg.get("From", ""),
            "to": msg.get("To", ""),
            "subject": msg.get("Subject", ""),
            "message_id": msg.get("Message-ID", ""),
            "in_reply_to": msg.get("In-Reply-To", ""),
            "references": msg.get("References", ""),
            "body": self._mime_body(msg),
        }

    def _mime_body(self, msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return self._decode_part(part)
            return ""
        return self._decode_part(msg)

    def _decode_part(self, part: email.message.Message) -> str:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, "replace")
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""

    def _first(self, value: Any) -> str:
        if isinstance(value, list):
            return str(value[0]) if value else ""
        return str(value) if value else ""

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        match cmd:
            case Post(thread_id=tid, text=text):
                return Result.ok(self._email_req(conn, self._to(tid), text))

            case Reply(thread_id=tid, reply_to=rid, text=text):
                return Result.ok(
                    self._email_req(conn, self._to(tid), text, in_reply_to=rid, references=rid)
                )

            case SendBlocks(thread_id=tid, blocks=blocks, text=text):
                body = text or self._blocks_to_text(blocks)
                return Result.ok(self._email_req(conn, self._to(tid), body))

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                atts = [
                    {
                        "filename": att.filename,
                        "url": att.url,
                        "mime_type": att.mime_type,
                    }
                ]
                return Result.ok(
                    self._email_req(conn, self._to(tid), caption, attachments=atts)
                )

            case _:
                tag = getattr(cmd, "tag", "unknown")
                return Result.err(
                    AdapterError(
                        reason=f"Email adapter does not support command: {tag}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def overlap_key(self, event: Event) -> str:
        return str(event.thread_id)

    def capabilities(self) -> frozenset[str]:
        return capabilities_of(self.name)

    def format(self, text: str) -> str:
        """Email bodies are plaintext; passthrough."""
        return text

    def encode_thread(self, address: str) -> ThreadId:
        return ThreadId(f"email:{address.lower()}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _to(self, thread_id: ThreadId) -> str:
        return self.decode_thread(thread_id)

    def _subject(self, conn: Connection) -> str:
        return conn.config.get("default_subject", "") or "(no subject)"

    def _email_req(
        self,
        conn: Connection,
        to: str,
        body: str,
        *,
        in_reply_to: str = "",
        references: str = "",
        attachments: list[dict[str, str]] | None = None,
    ) -> Sent:
        return Sent(
            raw={
                "transport": "smtp",
                "native": "sendmail",
                "email": {
                    "from": conn.config.get("from_address", ""),
                    "to": to,
                    "subject": self._subject(conn),
                    "body": body,
                    "in_reply_to": in_reply_to,
                    "references": references,
                    "attachments": attachments or [],
                },
            }
        )

    def _blocks_to_text(self, blocks: tuple[Block, ...]) -> str:
        lines: list[str] = []
        for b in blocks:
            content = getattr(b, "content", {})
            if content.get("text"):
                lines.append(str(content["text"]))
        return "\n".join(lines)
