"""Tests for SMTP and TwiML transports.

No network: SmtpTransport uses a recording sender; TwimlTransport is pure.
Both are driven with the real adapters' execute() output.
"""

from __future__ import annotations

from email.message import EmailMessage

from caspian.adapters.email import EmailAdapter
from caspian.adapters.voice import VoiceAdapter
from caspian.core.commands import Post, Reply
from caspian.core.ports import Connection, Sent
from caspian.core.types import ConnectionId, ThreadId
from caspian.interpreters.smtp import SmtpTransport
from caspian.interpreters.transport import TwimlTransport


class RecordingSender:
    """Captures EmailMessages instead of sending them over the network."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def __call__(self, message: EmailMessage) -> None:
        self.sent.append(message)


class TestSmtpTransport:
    def _email_conn(self) -> Connection:
        return Connection(
            id=ConnectionId("c1"),
            channel="email",
            config={"from_address": "bot@example.com", "default_subject": "Hello"},
        )

    def test_dispatches_email_adapter_output(self) -> None:
        adapter = EmailAdapter()
        exec_result = adapter.execute(
            Post(thread_id=ThreadId("email:user@dest.com"), text="body text"),
            self._email_conn(),
        )
        assert exec_result.is_ok

        sender = RecordingSender()
        transport = SmtpTransport(sender=sender)
        result = transport.dispatch(exec_result.value)

        assert result.is_ok
        assert len(sender.sent) == 1
        message = sender.sent[0]
        assert message["To"] == "user@dest.com"
        assert message["Subject"] == "Hello"
        assert message["From"] == "bot@example.com"
        assert message.get_content().strip() == "body text"
        assert result.value.raw == {"native": "sendmail"}

    def test_reply_sets_threading_headers(self) -> None:
        adapter = EmailAdapter()
        exec_result = adapter.execute(
            Reply(
                thread_id=ThreadId("email:user@dest.com"),
                reply_to="<abc@dest.com>",
                text="re: hi",
            ),
            self._email_conn(),
        )
        sender = RecordingSender()
        SmtpTransport(sender=sender).dispatch(exec_result.value)

        message = sender.sent[0]
        assert message["In-Reply-To"] == "<abc@dest.com>"
        assert message["References"] == "<abc@dest.com>"

    def test_non_smtp_passthrough(self) -> None:
        sender = RecordingSender()
        transport = SmtpTransport(sender=sender)
        sent = Sent(raw={"transport": "http_json", "native": "sendMessage"})

        result = transport.dispatch(sent)

        assert result.is_ok
        assert result.value is sent
        assert sender.sent == []

    def test_sender_exception_becomes_adapter_error(self) -> None:
        def boom(message: EmailMessage) -> None:
            raise OSError("connection refused")

        adapter = EmailAdapter()
        exec_result = adapter.execute(
            Post(thread_id=ThreadId("email:user@dest.com"), text="hi"),
            self._email_conn(),
        )
        transport = SmtpTransport(sender=boom)
        result = transport.dispatch(exec_result.value)

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"


class TestTwimlTransport:
    def test_surfaces_twiml_from_voice_adapter(self) -> None:
        adapter = VoiceAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="voice", config={})
        exec_result = adapter.execute(
            Post(thread_id=ThreadId("voice:CA1"), text="hello caller"), conn
        )
        assert exec_result.is_ok

        result = TwimlTransport().dispatch(exec_result.value)

        assert result.is_ok
        assert result.value.raw["native"] == "twiml"
        assert "<Say>hello caller</Say>" in result.value.raw["twiml"]

    def test_non_twiml_passthrough(self) -> None:
        sent = Sent(raw={"transport": "http_json"})
        result = TwimlTransport().dispatch(sent)

        assert result.is_ok
        assert result.value is sent
