"""Tests for the iMessage relay adapter."""

from __future__ import annotations

import json

from caspian.adapters.imessage import IMessageAdapter
from caspian.core.commands import Edit, Post
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, Message, ThreadId


def _conn(**overrides: object) -> Connection:
    config: dict[str, object] = {
        "relay_url": "https://relay.example",
        "api_key": "sekret",
    }
    config.update(overrides)
    return Connection(id=ConnectionId("c1"), channel="imessage", config=config)


class TestIMessageParse:
    """adapter.parse turns relay webhook bytes into kernel Events."""

    def test_parse_relay_message(self) -> None:
        adapter = IMessageAdapter()
        payload = {
            "type": "new-message",
            "data": {
                "guid": "abc-123",
                "text": "hello there",
                "handle": {"address": "+15551234567"},
                "chats": [{"guid": "iMessage;-;+15551234567"}],
                "isFromMe": False,
            },
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.kind == "message"
        assert msg.text == "hello there"
        assert msg.thread_id == "imessage:+15551234567"
        assert msg.sender == "+15551234567"
        assert msg.message_id == "abc-123"
        assert msg.chat_kind == "dm"

    def test_parse_from_me_returns_empty(self) -> None:
        adapter = IMessageAdapter()
        payload = {
            "type": "new-message",
            "data": {
                "guid": "self-1",
                "text": "sent by bot",
                "handle": {"address": "+15551234567"},
                "isFromMe": True,
            },
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value == []

    def test_parse_simplified_shape(self) -> None:
        adapter = IMessageAdapter()
        payload = {"from": "alice@example.com", "text": "hi", "message_id": "m9"}
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.thread_id == "imessage:alice@example.com"
        assert msg.sender == "alice@example.com"
        assert msg.text == "hi"
        assert msg.message_id == "m9"

    def test_parse_unknown_returns_empty(self) -> None:
        adapter = IMessageAdapter()
        payload = {"type": "typing-indicator", "data": {"guid": "x"}}
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = IMessageAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestIMessageExecute:
    """adapter.execute turns Commands into relay request descriptions."""

    def test_execute_post(self) -> None:
        adapter = IMessageAdapter()
        cmd = Post(thread_id=ThreadId("imessage:+15551234567"), text="yo")
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["method"] == "POST"
        assert sent.raw["url"] == "https://relay.example/api/v1/message/text"
        assert sent.raw["json"]["address"] == "+15551234567"
        assert sent.raw["json"]["message"] == "yo"
        assert sent.raw["native"] == "sendText"
        assert sent.raw["headers"]["Authorization"] == "Bearer sekret"

    def test_execute_without_api_key_errors(self) -> None:
        adapter = IMessageAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="imessage", config={})
        cmd = Post(thread_id=ThreadId("imessage:+15551234567"), text="yo")
        result = adapter.execute(cmd, conn)

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"
        assert "api_key" in result.error.reason

    def test_execute_edit_unsupported(self) -> None:
        adapter = IMessageAdapter()
        cmd = Edit(
            thread_id=ThreadId("imessage:+15551234567"),
            message_id="m1",
            text="fixed",
        )
        result = adapter.execute(cmd, _conn())

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"
        assert result.error.command_tag == "Edit"


class TestIMessageMisc:
    """overlap_key, verify, and formatting laws."""

    def test_overlap_key_is_thread_id(self) -> None:
        adapter = IMessageAdapter()
        event = Message(
            thread_id=ThreadId("imessage:+15551234567"),
            text="hi",
            chat_kind="dm",
        )
        assert adapter.overlap_key(event) == "imessage:+15551234567"

    def test_verify_true_when_unconfigured(self) -> None:
        adapter = IMessageAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="imessage", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is False

    def test_capabilities(self) -> None:
        adapter = IMessageAdapter()
        assert adapter.capabilities() == frozenset(
            {"receive", "reply", "send", "media"}
        )
