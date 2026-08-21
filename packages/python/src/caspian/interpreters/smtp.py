"""SMTP transport — dispatches the email adapter's "smtp" request-descriptions.

The email adapter builds a pure Sent.raw of the form
{"transport": "smtp", "native": "sendmail", "email": {from,to,subject,body,
in_reply_to,references,attachments}}. This transport turns that into a stdlib
EmailMessage and hands it to an injected sender callable. The default sender
uses smtplib; tests inject a recording sender so no network I/O occurs.

All failure is returned as Result.err(AdapterError) — never raised across the
boundary.
"""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from caspian.core.errors import AdapterError
from caspian.core.ports import Result, Sent

Sender = Callable[[EmailMessage], None]


class SmtpTransport:
    """Sends "smtp" request-descriptions; passes everything else through."""

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 25,
        user: str = "",
        password: str = "",
        use_tls: bool = False,
        sender: Sender | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._use_tls = use_tls
        self._sender: Sender = sender or self._default_sender

    def dispatch(self, sent: Sent) -> Result:
        """Send the email described in sent.raw, or pass non-smtp Sents through."""
        if sent.raw.get("transport") != "smtp":
            return Result.ok(sent)

        email = sent.raw.get("email")
        if not isinstance(email, dict):
            return Result.err(
                AdapterError(reason="smtp: missing email payload", command_tag="sendmail")
            )

        message = self._build_message(email)
        try:
            self._sender(message)
        except (smtplib.SMTPException, OSError) as e:
            return Result.err(
                AdapterError(reason=f"SMTP send failed: {e}", command_tag="sendmail")
            )

        return Result.ok(Sent(message_id="", raw={"native": "sendmail"}))

    def _build_message(self, email: dict[str, Any]) -> EmailMessage:
        message = EmailMessage()
        message["From"] = str(email.get("from", ""))
        message["To"] = str(email.get("to", ""))
        message["Subject"] = str(email.get("subject", ""))
        in_reply_to = str(email.get("in_reply_to", ""))
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        references = str(email.get("references", ""))
        if references:
            message["References"] = references
        message.set_content(str(email.get("body", "")))
        return message

    def _default_sender(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port) as client:
            if self._use_tls:
                client.starttls()
            if self._user:
                client.login(self._user, self._password)
            client.send_message(message)


__all__ = ["Sender", "SmtpTransport"]
