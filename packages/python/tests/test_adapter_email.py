"""Tests for the Email adapter."""

from __future__ import annotations

import json

from caspian.adapters.email import EmailAdapter
from caspian.core.commands import Post, React, Reply, SendMedia
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import Attachment, ConnectionId, Message, ThreadId


class TestEmailParse:
    """adapter.parse turns inbound email payloads into kernel Events."""

    def test_parse_simplified_inbound(self) -> None:
        adapter = EmailAdapter()
        payload = {
            "from": "Alice <Alice@Example.com>",
            "to": "bot@caspian.dev",
            "subject": "Hello",
            "body": "hi there",
            "message_id": "<abc@example.com>",
            "in_reply_to": "<prev@example.com>",
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.kind == "message"
        assert msg.text == "hi there"
        assert msg.chat_kind == "dm"
        assert msg.sender == "alice@example.com"
        assert msg.thread_id == "email:alice@example.com"
        assert msg.message_id == "<abc@example.com>"
        assert msg.reply_to == "<prev@example.com>"

    def test_parse_sns_wrapped_best_effort(self) -> None:
        adapter = EmailAdapter()
        content = (
            "From: Bob <bob@example.com>\r\n"
            "To: bot@caspian.dev\r\n"
            "Subject: Re: Ticket\r\n"
            "Message-ID: <m1@example.com>\r\n"
            "In-Reply-To: <orig@example.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "please help me"
        )
        inner = {
            "notificationType": "Received",
            "mail": {
                "source": "bob@example.com",
                "destination": ["bot@caspian.dev"],
                "messageId": "ses-123",
                "commonHeaders": {
                    "from": ["Bob <bob@example.com>"],
                    "to": ["bot@caspian.dev"],
                    "subject": "Re: Ticket",
                    "messageId": "<m1@example.com>",
                },
            },
            "content": content,
        }
        notification = {"Type": "Notification", "Message": json.dumps(inner)}
        result = adapter.parse(RawInbound(body=json.dumps(notification).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.sender == "bob@example.com"
        assert msg.thread_id == "email:bob@example.com"
        assert msg.text == "please help me"
        assert msg.message_id == "<m1@example.com>"
        assert msg.reply_to == "<orig@example.com>"

    def test_parse_unknown_object_returns_empty(self) -> None:
        adapter = EmailAdapter()
        result = adapter.parse(RawInbound(body=json.dumps({"foo": "bar"}).encode()))
        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = EmailAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestEmailExecute:
    """adapter.execute turns Commands into smtp request-descriptions."""

    def _conn(self) -> Connection:
        return Connection(
            id=ConnectionId("c1"),
            channel="email",
            config={"from_address": "bot@caspian.dev", "default_subject": "Support"},
        )

    def test_execute_post(self) -> None:
        adapter = EmailAdapter()
        cmd = Post(thread_id=ThreadId("email:alice@example.com"), text="hello")
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        raw = result.value.raw
        assert raw["transport"] == "smtp"
        assert raw["native"] == "sendmail"
        assert raw["email"]["from"] == "bot@caspian.dev"
        assert raw["email"]["to"] == "alice@example.com"
        assert raw["email"]["subject"] == "Support"
        assert raw["email"]["body"] == "hello"
        assert raw["email"]["in_reply_to"] == ""

    def test_execute_post_default_subject_fallback(self) -> None:
        adapter = EmailAdapter()
        conn = Connection(
            id=ConnectionId("c2"),
            channel="email",
            config={"from_address": "bot@caspian.dev"},
        )
        cmd = Post(thread_id=ThreadId("email:alice@example.com"), text="hi")
        result = adapter.execute(cmd, conn)
        assert result.is_ok
        assert result.value.raw["email"]["subject"] == "(no subject)"

    def test_execute_reply_sets_in_reply_to(self) -> None:
        adapter = EmailAdapter()
        cmd = Reply(
            thread_id=ThreadId("email:alice@example.com"),
            reply_to="<orig@example.com>",
            text="re",
        )
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        e = result.value.raw["email"]
        assert e["in_reply_to"] == "<orig@example.com>"
        assert e["references"] == "<orig@example.com>"
        assert e["to"] == "alice@example.com"

    def test_execute_send_media_attachments(self) -> None:
        adapter = EmailAdapter()
        cmd = SendMedia(
            thread_id=ThreadId("email:alice@example.com"),
            attachment=Attachment(
                type="file",
                url="https://x/y.pdf",
                filename="y.pdf",
                mime_type="application/pdf",
            ),
            caption="see attached",
        )
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        e = result.value.raw["email"]
        assert e["body"] == "see attached"
        assert e["attachments"] == [
            {"filename": "y.pdf", "url": "https://x/y.pdf", "mime_type": "application/pdf"}
        ]

    def test_execute_react_returns_adapter_error(self) -> None:
        adapter = EmailAdapter()
        cmd = React(
            thread_id=ThreadId("email:alice@example.com"),
            message_id="<m1@example.com>",
            emoji="👍",
        )
        result = adapter.execute(cmd, self._conn())
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"
        assert result.error.command_tag == "React"


class TestEmailMisc:
    """overlap_key, capabilities, verify, format."""

    def test_overlap_key(self) -> None:
        adapter = EmailAdapter()
        event = Message(
            thread_id=ThreadId("email:alice@example.com"),
            text="hi",
            chat_kind="dm",
        )
        assert adapter.overlap_key(event) == "email:alice@example.com"

    def test_capabilities(self) -> None:
        adapter = EmailAdapter()
        assert adapter.capabilities() == frozenset(
            {"receive", "reply", "send", "media", "threading"}
        )

    def test_verify_true(self) -> None:
        adapter = EmailAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="email", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is True

    def test_format_passthrough(self) -> None:
        adapter = EmailAdapter()
        assert adapter.format("plain *text*") == "plain *text*"

    def test_encode_decode_thread(self) -> None:
        adapter = EmailAdapter()
        tid = adapter.encode_thread("Alice@Example.com")
        assert tid == "email:alice@example.com"
        assert adapter.decode_thread(tid) == "alice@example.com"
