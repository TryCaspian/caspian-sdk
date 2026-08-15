"""Tests for the Telegram adapter."""

from __future__ import annotations

import json

from caspian.adapters.telegram import TelegramAdapter
from caspian.core.commands import Post, Typing
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, ThreadId


class TestTelegramParse:
    """adapter.parse turns Update bytes into kernel Events."""

    def test_parse_text_message(self) -> None:
        adapter = TelegramAdapter()
        update = {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "from": {"id": 100, "first_name": "Alice"},
                "chat": {"id": 555, "type": "private"},
                "text": "hello",
            },
        }
        raw = RawInbound(body=json.dumps(update).encode())
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "message"
        assert events[0].text == "hello"
        assert events[0].thread_id == "telegram:555"
        assert events[0].chat_kind == "dm"

    def test_parse_callback_query(self) -> None:
        adapter = TelegramAdapter()
        update = {
            "update_id": 2,
            "callback_query": {
                "id": "cb1",
                "from": {"id": 100},
                "message": {"chat": {"id": 555, "type": "private"}},
                "data": "done",
            },
        }
        raw = RawInbound(body=json.dumps(update).encode())
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "action"
        assert events[0].data == "done"

    def test_parse_unknown_update_returns_empty(self) -> None:
        adapter = TelegramAdapter()
        update = {"update_id": 3, "edited_message": {}}
        raw = RawInbound(body=json.dumps(update).encode())
        result = adapter.parse(raw)

        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = TelegramAdapter()
        raw = RawInbound(body=b"not json")
        result = adapter.parse(raw)

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestTelegramExecute:
    """adapter.execute turns Commands into Bot API payloads."""

    def _conn(self) -> Connection:
        return Connection(
            id=ConnectionId("conn1"),
            channel="telegram",
            config={"bot_token": "123:ABC"},
        )

    def test_execute_post(self) -> None:
        adapter = TelegramAdapter()
        cmd = Post(thread_id=ThreadId("telegram:555"), text="hi")
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["method"] == "sendMessage"
        assert sent.raw["chat_id"] == "555"
        assert sent.raw["text"] == "hi"

    def test_execute_typing(self) -> None:
        adapter = TelegramAdapter()
        cmd = Typing(thread_id=ThreadId("telegram:555"))
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        assert result.value.raw["method"] == "sendChatAction"

    def test_execute_without_token_errors(self) -> None:
        adapter = TelegramAdapter()
        cmd = Post(thread_id=ThreadId("telegram:555"), text="hi")
        conn = Connection(id=ConnectionId("c1"), channel="telegram", config={})
        result = adapter.execute(cmd, conn)

        assert not result.is_ok
        assert "bot_token" in (result.error.reason if result.error else "")


class TestTelegramOverlapKey:
    """overlap_key returns the thread_id (chat-level granularity for Telegram)."""

    def test_overlap_key(self) -> None:
        from caspian.core.types import Message

        adapter = TelegramAdapter()
        event = Message(
            thread_id=ThreadId("telegram:555"),
            text="hi",
            chat_kind="dm",
        )
        assert adapter.overlap_key(event) == "telegram:555"
