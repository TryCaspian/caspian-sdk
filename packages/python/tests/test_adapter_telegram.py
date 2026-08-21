"""Tests for the Telegram adapter."""

from __future__ import annotations

import json

from caspian.adapters.telegram import TelegramAdapter
from caspian.core.commands import (
    Delete,
    Forward,
    Pin,
    Post,
    Reply,
    SendMedia,
    Typing,
)
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import Action, Attachment, Button, ConnectionId, ThreadId


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
        update = {"update_id": 3, "poll_answer": {"poll_id": "x"}}
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
        assert sent.raw["native"] == "sendMessage"
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["json"]["chat_id"] == "555"
        assert sent.raw["json"]["text"] == "hi"
        assert "sendMessage" in sent.raw["url"]

    def test_execute_typing(self) -> None:
        adapter = TelegramAdapter()
        cmd = Typing(thread_id=ThreadId("telegram:555"))
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        assert result.value.raw["native"] == "sendChatAction"

    def test_execute_without_token_errors(self) -> None:
        adapter = TelegramAdapter()
        cmd = Post(thread_id=ThreadId("telegram:555"), text="hi")
        conn = Connection(id=ConnectionId("c1"), channel="telegram", config={})
        result = adapter.execute(cmd, conn)

        assert not result.is_ok
        assert "bot_token" in (result.error.reason if result.error else "")


class TestTelegramNewCommands:
    """Full-parity commands: media, reply, delete, pin, forward."""

    def _conn(self) -> Connection:
        return Connection(
            id=ConnectionId("c1"), channel="telegram", config={"bot_token": "123:ABC"}
        )

    def test_send_media_photo(self) -> None:
        adapter = TelegramAdapter()
        cmd = SendMedia(
            thread_id=ThreadId("telegram:555"),
            attachment=Attachment(type="photo", url="https://x/y.png"),
            caption="hey",
        )
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        assert result.value.raw["native"] == "sendPhoto"
        assert result.value.raw["json"]["photo"] == "https://x/y.png"
        assert result.value.raw["json"]["caption"] == "hey"

    def test_reply_sets_reply_parameters(self) -> None:
        adapter = TelegramAdapter()
        cmd = Reply(thread_id=ThreadId("telegram:555"), reply_to="42", text="ok")
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        assert result.value.raw["json"]["reply_parameters"] == {"message_id": 42}

    def test_delete(self) -> None:
        adapter = TelegramAdapter()
        cmd = Delete(thread_id=ThreadId("telegram:555"), message_id="42")
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        assert result.value.raw["native"] == "deleteMessage"

    def test_pin(self) -> None:
        adapter = TelegramAdapter()
        cmd = Pin(thread_id=ThreadId("telegram:555"), message_id="42")
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        assert result.value.raw["native"] == "pinChatMessage"

    def test_forward(self) -> None:
        adapter = TelegramAdapter()
        cmd = Forward(
            from_thread_id=ThreadId("telegram:1"),
            to_thread_id=ThreadId("telegram:2"),
            message_id="42",
        )
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        assert result.value.raw["json"]["from_chat_id"] == "1"
        assert result.value.raw["json"]["chat_id"] == "2"

    def test_post_with_buttons(self) -> None:
        adapter = TelegramAdapter()
        cmd = Post(
            thread_id=ThreadId("telegram:555"),
            text="pick",
            actions=(Button(label="Yes", data="yes"),),
        )
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        kb = result.value.raw["json"]["reply_markup"]["inline_keyboard"]
        assert kb[0][0]["text"] == "Yes"
        assert kb[0][0]["callback_data"] == "yes"


class TestTelegramParseRich:
    """Media, reply, topics, edited, membership parsing."""

    def test_parse_photo_attachment(self) -> None:
        adapter = TelegramAdapter()
        update = {
            "message": {
                "message_id": 5,
                "from": {"id": 1},
                "chat": {"id": 555, "type": "private"},
                "caption": "look",
                "photo": [{"file_id": "small"}, {"file_id": "big", "file_size": 100}],
            }
        }
        result = adapter.parse(RawInbound(body=json.dumps(update).encode()))
        assert result.is_ok
        msg = result.value[0]
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "photo"
        assert msg.attachments[0].file_id == "big"

    def test_parse_reply_to(self) -> None:
        adapter = TelegramAdapter()
        update = {
            "message": {
                "message_id": 6,
                "from": {"id": 1},
                "chat": {"id": 555, "type": "private"},
                "text": "re",
                "reply_to_message": {"message_id": 3},
            }
        }
        result = adapter.parse(RawInbound(body=json.dumps(update).encode()))
        assert result.value[0].reply_to == "3"

    def test_parse_edited(self) -> None:
        adapter = TelegramAdapter()
        update = {
            "edited_message": {
                "message_id": 7,
                "from": {"id": 1},
                "chat": {"id": 555, "type": "private"},
                "text": "fixed",
            }
        }
        result = adapter.parse(RawInbound(body=json.dumps(update).encode()))
        assert result.value[0].kind == "edited"
        assert result.value[0].text == "fixed"

    def test_parse_member_join(self) -> None:
        adapter = TelegramAdapter()
        update = {
            "message": {
                "message_id": 8,
                "chat": {"id": -100, "type": "group"},
                "new_chat_members": [{"id": 42}],
            }
        }
        result = adapter.parse(RawInbound(body=json.dumps(update).encode()))
        assert result.value[0].kind == "member_join"
        assert result.value[0].member == "42"


class TestTelegramVerifyAck:
    def test_verify_ok_when_no_secret(self) -> None:
        adapter = TelegramAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="telegram", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is False

    def test_verify_checks_secret(self) -> None:
        adapter = TelegramAdapter()
        conn = Connection(
            id=ConnectionId("c1"), channel="telegram", config={"webhook_secret": "s3cr3t"}
        )
        good = RawInbound(body=b"{}", headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"})
        bad = RawInbound(body=b"{}", headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
        assert adapter.verify(good, conn) is True
        assert adapter.verify(bad, conn) is False

    def test_acknowledge_callback(self) -> None:
        adapter = TelegramAdapter()
        conn = Connection(
            id=ConnectionId("c1"), channel="telegram", config={"bot_token": "123:ABC"}
        )
        event = Action(
            thread_id=ThreadId("telegram:555"), data="x", interaction_id="cb1"
        )
        ack = adapter.acknowledge(event, conn)
        assert ack is not None and ack.is_ok
        assert ack.value.raw["native"] == "answerCallbackQuery"


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
