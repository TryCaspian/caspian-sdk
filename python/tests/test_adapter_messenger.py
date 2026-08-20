"""Tests for the Facebook Messenger adapter."""

from __future__ import annotations

import hashlib
import hmac
import json

from caspian.adapters.messenger import MessengerAdapter
from caspian.core.commands import Post, React, Reply, SendMedia, Typing
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import Attachment, Button, ConnectionId, Message, ThreadId


def _conn() -> Connection:
    return Connection(
        id=ConnectionId("m1"),
        channel="messenger",
        config={"page_access_token": "PTKN"},
    )


def _messaging_webhook(messaging: dict) -> bytes:
    payload = {
        "object": "page",
        "entry": [{"id": "PAGE", "messaging": [messaging]}],
    }
    return json.dumps(payload).encode()


class TestMessengerParse:
    def test_parse_text_message(self) -> None:
        adapter = MessengerAdapter()
        raw = RawInbound(
            body=_messaging_webhook(
                {
                    "sender": {"id": "PSID1"},
                    "recipient": {"id": "PAGE"},
                    "message": {"mid": "mid.1", "text": "hello"},
                }
            )
        )
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "message"
        assert events[0].text == "hello"
        assert events[0].thread_id == "messenger:PSID1"
        assert events[0].chat_kind == "dm"
        assert events[0].message_id == "mid.1"

    def test_parse_postback(self) -> None:
        adapter = MessengerAdapter()
        raw = RawInbound(
            body=_messaging_webhook(
                {
                    "sender": {"id": "PSID1"},
                    "recipient": {"id": "PAGE"},
                    "postback": {"title": "Get Started", "payload": "START"},
                }
            )
        )
        result = adapter.parse(raw)

        assert result.is_ok
        event = result.value[0]
        assert event.kind == "action"
        assert event.data == "START"
        assert event.thread_id == "messenger:PSID1"

    def test_parse_unknown_returns_empty(self) -> None:
        adapter = MessengerAdapter()
        raw = RawInbound(
            body=_messaging_webhook(
                {"sender": {"id": "PSID1"}, "delivery": {"watermark": 1}}
            )
        )
        result = adapter.parse(raw)
        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = MessengerAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestMessengerExecute:
    def test_execute_post(self) -> None:
        adapter = MessengerAdapter()
        cmd = Post(thread_id=ThreadId("messenger:PSID1"), text="hi")
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["method"] == "POST"
        assert sent.raw["url"] == "https://graph.facebook.com/v21.0/me/messages"
        assert sent.raw["native"] == "message"
        assert sent.raw["headers"]["Authorization"] == "Bearer PTKN"
        body = sent.raw["json"]
        assert body["recipient"] == {"id": "PSID1"}
        assert body["message"] == {"text": "hi"}

    def test_execute_post_with_quick_replies(self) -> None:
        adapter = MessengerAdapter()
        cmd = Post(
            thread_id=ThreadId("messenger:PSID1"),
            text="pick",
            actions=(Button(label="Yes", data="y"),),
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        qr = result.value.raw["json"]["message"]["quick_replies"]
        assert qr[0] == {"content_type": "text", "title": "Yes", "payload": "y"}

    def test_execute_reply_ignores_reply_to(self) -> None:
        adapter = MessengerAdapter()
        cmd = Reply(
            thread_id=ThreadId("messenger:PSID1"),
            reply_to="mid.1",
            text="re",
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        body = result.value.raw["json"]
        assert body["message"] == {"text": "re"}
        assert "reply_to" not in json.dumps(body)

    def test_execute_send_media(self) -> None:
        adapter = MessengerAdapter()
        cmd = SendMedia(
            thread_id=ThreadId("messenger:PSID1"),
            attachment=Attachment(type="photo", url="https://x/y.png"),
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "attachment"
        att = sent.raw["json"]["message"]["attachment"]
        assert att["type"] == "image"
        assert att["payload"]["url"] == "https://x/y.png"

    def test_execute_typing(self) -> None:
        adapter = MessengerAdapter()
        cmd = Typing(thread_id=ThreadId("messenger:PSID1"))
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "typing_on"
        body = sent.raw["json"]
        assert body["recipient"] == {"id": "PSID1"}
        assert body["sender_action"] == "typing_on"

    def test_execute_without_token_errors(self) -> None:
        adapter = MessengerAdapter()
        cmd = Post(thread_id=ThreadId("messenger:PSID1"), text="hi")
        conn = Connection(id=ConnectionId("c"), channel="messenger", config={})
        result = adapter.execute(cmd, conn)
        assert not result.is_ok
        assert result.error is not None
        assert "page_access_token" in result.error.reason

    def test_execute_react_unsupported(self) -> None:
        adapter = MessengerAdapter()
        cmd = React(
            thread_id=ThreadId("messenger:PSID1"),
            message_id="mid.1",
            emoji="👍",
        )
        result = adapter.execute(cmd, _conn())
        assert not result.is_ok
        assert result.error is not None
        assert result.error.command_tag == "React"


class TestMessengerVerify:
    def test_verify_ok_when_no_secret(self) -> None:
        adapter = MessengerAdapter()
        conn = Connection(id=ConnectionId("c"), channel="messenger", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is True

    def test_verify_checks_signature(self) -> None:
        adapter = MessengerAdapter()
        conn = Connection(
            id=ConnectionId("c"),
            channel="messenger",
            config={"app_secret": "shh"},
        )
        body = b'{"entry":[]}'
        digest = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        good = RawInbound(
            body=body, headers={"X-Hub-Signature-256": "sha256=" + digest}
        )
        bad = RawInbound(body=body, headers={"X-Hub-Signature-256": "sha256=nope"})
        assert adapter.verify(good, conn) is True
        assert adapter.verify(bad, conn) is False


class TestMessengerMisc:
    def test_overlap_key(self) -> None:
        adapter = MessengerAdapter()
        event = Message(
            thread_id=ThreadId("messenger:PSID1"),
            text="hi",
            chat_kind="dm",
        )
        assert adapter.overlap_key(event) == "messenger:PSID1"

    def test_capabilities(self) -> None:
        adapter = MessengerAdapter()
        assert adapter.capabilities() == frozenset(
            {"receive", "reply", "send", "media", "buttons", "typing"}
        )

    def test_thread_roundtrip(self) -> None:
        adapter = MessengerAdapter()
        tid = adapter.encode_thread("PSID1")
        assert tid == "messenger:PSID1"
        assert adapter.decode_thread(tid) == "PSID1"
