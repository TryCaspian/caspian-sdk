"""Tests for the X / Twitter adapter."""

from __future__ import annotations

import json

from caspian.adapters.x import XAdapter
from caspian.core.commands import Post, Reply
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, Message, ThreadId


def _conn(**config: str) -> Connection:
    return Connection(id=ConnectionId("c1"), channel="x", config=dict(config))


class TestXParse:
    """adapter.parse turns Account Activity bytes into kernel Events."""

    def test_parse_dm_event(self) -> None:
        adapter = XAdapter()
        payload = {
            "direct_message_events": [
                {
                    "id": "dm1",
                    "message_create": {
                        "sender_id": "12345",
                        "message_data": {"text": "hey there"},
                    },
                }
            ]
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.kind == "message"
        assert msg.text == "hey there"
        assert msg.sender == "12345"
        assert msg.chat_kind == "dm"
        assert msg.thread_id == "x:dm:12345"

    def test_parse_simple_dm(self) -> None:
        adapter = XAdapter()
        payload = {"dm": {"from": "999", "text": "ping"}}
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value[0].chat_kind == "dm"
        assert result.value[0].text == "ping"
        assert result.value[0].thread_id == "x:dm:999"

    def test_parse_tweet_event(self) -> None:
        adapter = XAdapter()
        payload = {
            "tweet_create_events": [
                {"id": "t1", "text": "hello world", "user": {"id": "777"}}
            ]
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        msg = result.value[0]
        assert msg.text == "hello world"
        assert msg.chat_kind == "channel"
        assert msg.thread_id == "x:777"

    def test_parse_unknown_returns_empty(self) -> None:
        adapter = XAdapter()
        payload = {"favorite_events": [{"id": "f1"}]}
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = XAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestXExecute:
    """adapter.execute turns Commands into X API v2 payloads."""

    def test_execute_post_tweet(self) -> None:
        adapter = XAdapter()
        cmd = Post(thread_id=ThreadId("x:777"), text="gm")
        result = adapter.execute(cmd, _conn(bearer_token="TOKEN"))

        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "createTweet"
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["url"] == "https://api.twitter.com/2/tweets"
        assert sent.raw["json"] == {"text": "gm"}
        assert sent.raw["headers"]["Authorization"] == "Bearer TOKEN"

    def test_execute_reply_tweet(self) -> None:
        adapter = XAdapter()
        cmd = Reply(thread_id=ThreadId("x:777"), reply_to="42", text="re")
        result = adapter.execute(cmd, _conn(bearer_token="TOKEN"))

        assert result.is_ok
        body = result.value.raw["json"]
        assert body["text"] == "re"
        assert body["reply"]["in_reply_to_tweet_id"] == "42"

    def test_execute_dm_post(self) -> None:
        adapter = XAdapter()
        cmd = Post(thread_id=ThreadId("x:dm:12345"), text="hi")
        result = adapter.execute(cmd, _conn(bearer_token="TOKEN"))

        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "createDm"
        assert sent.raw["url"].endswith("/dm_conversations/with/12345/messages")
        assert sent.raw["json"] == {"text": "hi"}

    def test_execute_without_token_errors(self) -> None:
        adapter = XAdapter()
        cmd = Post(thread_id=ThreadId("x:777"), text="gm")
        result = adapter.execute(cmd, _conn())

        assert not result.is_ok
        assert result.error is not None
        assert "bearer_token" in result.error.reason
        assert result.error.command_tag == "Post"

    def test_execute_unsupported_command_errors(self) -> None:
        from caspian.core.commands import Delete

        adapter = XAdapter()
        cmd = Delete(thread_id=ThreadId("x:777"), message_id="1")
        result = adapter.execute(cmd, _conn(bearer_token="TOKEN"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.command_tag == "Delete"


class TestXMisc:
    def test_overlap_key(self) -> None:
        adapter = XAdapter()
        event = Message(thread_id=ThreadId("x:777"), text="hi", chat_kind="channel")
        assert adapter.overlap_key(event) == "x:777"

    def test_capabilities(self) -> None:
        adapter = XAdapter()
        assert adapter.capabilities() == frozenset({"receive", "send", "reply", "dm"})

    def test_verify_true_when_unconfigured(self) -> None:
        adapter = XAdapter()
        assert adapter.verify(RawInbound(body=b"{}"), _conn()) is True

    def test_thread_roundtrip(self) -> None:
        adapter = XAdapter()
        assert adapter.decode_thread(adapter.encode_thread("5", "dm")) == ("dm", "5")
        assert adapter.decode_thread(adapter.encode_thread("5")) == ("tweet", "5")
