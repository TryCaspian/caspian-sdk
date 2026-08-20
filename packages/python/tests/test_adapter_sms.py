"""Tests for the Twilio SMS adapter."""

from __future__ import annotations

from urllib.parse import urlencode

from caspian.adapters.sms import SmsAdapter
from caspian.core.commands import Post, React
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, Message, ThreadId


def _inbound(fields: dict[str, str]) -> RawInbound:
    return RawInbound(body=urlencode(fields).encode())


class TestSmsParse:
    """adapter.parse turns Twilio form bytes into kernel Events."""

    def test_parse_form_inbound(self) -> None:
        adapter = SmsAdapter()
        raw = _inbound(
            {
                "From": "+15551234567",
                "To": "+15559876543",
                "Body": "hello there",
                "MessageSid": "SM123",
            }
        )
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.kind == "message"
        assert msg.text == "hello there"
        assert msg.thread_id == "sms:+15551234567"
        assert msg.sender == "+15551234567"
        assert msg.message_id == "SM123"
        assert msg.chat_kind == "dm"

    def test_parse_media(self) -> None:
        adapter = SmsAdapter()
        raw = _inbound(
            {
                "From": "+15551234567",
                "Body": "pic",
                "MessageSid": "SM1",
                "NumMedia": "1",
                "MediaUrl0": "https://example.com/img.jpg",
                "MediaContentType0": "image/jpeg",
            }
        )
        result = adapter.parse(raw)
        assert result.is_ok
        msg = result.value[0]
        assert len(msg.attachments) == 1
        assert msg.attachments[0].url == "https://example.com/img.jpg"

    def test_parse_no_from_returns_empty(self) -> None:
        adapter = SmsAdapter()
        result = adapter.parse(_inbound({"Body": "orphan"}))
        assert result.is_ok
        assert result.value == []


class TestSmsExecute:
    """adapter.execute turns Commands into Messages API form payloads."""

    def _conn(self) -> Connection:
        return Connection(
            id=ConnectionId("c1"),
            channel="sms",
            config={
                "account_sid": "AC123",
                "auth_token": "tok",
                "from_number": "+15559876543",
            },
        )

    def test_execute_post(self) -> None:
        adapter = SmsAdapter()
        cmd = Post(thread_id=ThreadId("sms:+15551234567"), text="hi")
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["transport"] == "http_form"
        assert sent.raw["method"] == "POST"
        assert "Messages.json" in sent.raw["url"]
        form = sent.raw["form"]
        assert form["To"] == "+15551234567"
        assert form["From"] == "+15559876543"
        assert form["Body"] == "hi"
        assert sent.raw["headers"]["Authorization"].startswith("Basic ")

    def test_execute_without_creds_errors(self) -> None:
        adapter = SmsAdapter()
        cmd = Post(thread_id=ThreadId("sms:+15551234567"), text="hi")
        conn = Connection(id=ConnectionId("c1"), channel="sms", config={})
        result = adapter.execute(cmd, conn)

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"

    def test_execute_react_errors(self) -> None:
        adapter = SmsAdapter()
        cmd = React(
            thread_id=ThreadId("sms:+15551234567"), message_id="SM1", emoji="👍"
        )
        result = adapter.execute(cmd, self._conn())

        assert not result.is_ok
        assert result.error is not None
        assert result.error.command_tag == "React"


class TestSmsMisc:
    def test_overlap_key(self) -> None:
        adapter = SmsAdapter()
        event = Message(
            thread_id=ThreadId("sms:+15551234567"), text="hi", chat_kind="dm"
        )
        assert adapter.overlap_key(event) == "sms:+15551234567"

    def test_capabilities(self) -> None:
        adapter = SmsAdapter()
        assert adapter.capabilities() == frozenset(
            {"receive", "reply", "send", "media"}
        )

    def test_verify_true_when_unconfigured(self) -> None:
        adapter = SmsAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="sms", config={})
        assert adapter.verify(_inbound({"From": "+1"}), conn) is True
