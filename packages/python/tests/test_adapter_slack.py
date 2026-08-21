"""Tests for the Slack adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse

from caspian.adapters.slack import SlackAdapter
from caspian.core.commands import Post, React, Reply
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, Message, ThreadId


def _event_callback(inner: dict) -> bytes:
    return json.dumps({"type": "event_callback", "event": inner}).encode()


class TestSlackParse:
    """adapter.parse turns Events API bytes into kernel Events."""

    def test_parse_text_message(self) -> None:
        adapter = SlackAdapter()
        raw = RawInbound(
            body=_event_callback(
                {
                    "type": "message",
                    "user": "U1",
                    "channel": "C1",
                    "ts": "1360782400.498405",
                    "text": "hello",
                }
            )
        )
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "message"
        assert events[0].text == "hello"
        assert events[0].thread_id == "slack:C1"
        assert events[0].chat_kind == "channel"

    def test_parse_threaded_message(self) -> None:
        adapter = SlackAdapter()
        raw = RawInbound(
            body=_event_callback(
                {
                    "type": "message",
                    "user": "U1",
                    "channel": "C1",
                    "ts": "2.0",
                    "thread_ts": "1.0",
                    "text": "re",
                }
            )
        )
        result = adapter.parse(raw)
        assert result.is_ok
        assert result.value[0].thread_id == "slack:C1:1.0"

    def test_parse_skips_bot_message(self) -> None:
        adapter = SlackAdapter()
        raw = RawInbound(
            body=_event_callback(
                {"type": "message", "bot_id": "B1", "channel": "C1", "text": "x"}
            )
        )
        result = adapter.parse(raw)
        assert result.is_ok
        assert result.value == []

    def test_parse_block_actions_to_action(self) -> None:
        adapter = SlackAdapter()
        payload = {
            "type": "block_actions",
            "user": {"id": "U9"},
            "trigger_id": "trig1",
            "channel": {"id": "C1"},
            "message": {"ts": "5.0"},
            "actions": [{"action_id": "approve", "value": "v1"}],
        }
        raw = RawInbound(body=json.dumps(payload).encode())
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "action"
        assert events[0].data == "approve"
        assert events[0].interaction_id == "trig1"
        assert events[0].thread_id == "slack:C1"

    def test_parse_block_actions_form_encoded(self) -> None:
        adapter = SlackAdapter()
        payload = {
            "type": "block_actions",
            "user": {"id": "U9"},
            "channel": {"id": "C1"},
            "message": {"ts": "5.0"},
            "actions": [{"action_id": "click", "value": "v"}],
        }
        body = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()
        result = adapter.parse(RawInbound(body=body))
        assert result.is_ok
        assert result.value[0].kind == "action"
        assert result.value[0].data == "click"

    def test_parse_reaction_added(self) -> None:
        adapter = SlackAdapter()
        raw = RawInbound(
            body=_event_callback(
                {
                    "type": "reaction_added",
                    "user": "U1",
                    "reaction": "thumbsup",
                    "item": {"type": "message", "channel": "C1", "ts": "5.0"},
                }
            )
        )
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "reaction"
        assert events[0].emoji == "thumbsup"
        assert events[0].removed is False
        assert events[0].message_id == "5.0"

    def test_parse_reaction_removed(self) -> None:
        adapter = SlackAdapter()
        raw = RawInbound(
            body=_event_callback(
                {
                    "type": "reaction_removed",
                    "user": "U1",
                    "reaction": "eyes",
                    "item": {"type": "message", "channel": "C1", "ts": "5.0"},
                }
            )
        )
        result = adapter.parse(raw)
        assert result.is_ok
        assert result.value[0].removed is True

    def test_parse_url_verification_returns_empty(self) -> None:
        adapter = SlackAdapter()
        body = json.dumps(
            {"type": "url_verification", "challenge": "abc123"}
        ).encode()
        result = adapter.parse(RawInbound(body=body))

        assert result.is_ok
        assert result.value == []

    def test_parse_unknown_type_returns_empty(self) -> None:
        adapter = SlackAdapter()
        body = json.dumps({"type": "team_join"}).encode()
        result = adapter.parse(RawInbound(body=body))
        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = SlackAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestSlackExecute:
    """adapter.execute turns Commands into Slack Web API payloads."""

    def _conn(self) -> Connection:
        return Connection(
            id=ConnectionId("conn1"),
            channel="slack",
            config={"bot_token": "xoxb-123"},
        )

    def test_execute_post(self) -> None:
        adapter = SlackAdapter()
        cmd = Post(thread_id=ThreadId("slack:C1"), text="hi")
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "chat.postMessage"
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["url"] == "https://slack.com/api/chat.postMessage"
        assert sent.raw["json"]["channel"] == "C1"
        assert sent.raw["json"]["text"] == "hi"
        assert sent.raw["headers"]["Authorization"] == "Bearer xoxb-123"

    def test_execute_reply_sets_thread_ts(self) -> None:
        adapter = SlackAdapter()
        cmd = Reply(
            thread_id=ThreadId("slack:C1"), reply_to="1360782400.498405", text="ok"
        )
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        assert result.value.raw["native"] == "chat.postMessage"
        assert result.value.raw["json"]["thread_ts"] == "1360782400.498405"

    def test_execute_react(self) -> None:
        adapter = SlackAdapter()
        cmd = React(
            thread_id=ThreadId("slack:C1"), message_id="5.0", emoji="thumbsup"
        )
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "reactions.add"
        assert sent.raw["json"]["channel"] == "C1"
        assert sent.raw["json"]["timestamp"] == "5.0"
        assert sent.raw["json"]["name"] == "thumbsup"

    def test_execute_without_token_errors(self) -> None:
        adapter = SlackAdapter()
        cmd = Post(thread_id=ThreadId("slack:C1"), text="hi")
        conn = Connection(id=ConnectionId("c1"), channel="slack", config={})
        result = adapter.execute(cmd, conn)

        assert not result.is_ok
        assert result.error is not None
        assert "bot_token" in result.error.reason


class TestSlackVerify:
    def test_verify_ok_when_no_secret(self) -> None:
        adapter = SlackAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="slack", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is False

    def test_verify_checks_signature(self) -> None:
        adapter = SlackAdapter()
        secret = "s3cr3t"
        conn = Connection(
            id=ConnectionId("c1"),
            channel="slack",
            config={"signing_secret": secret},
        )
        body = b'{"type":"event_callback"}'
        ts = "1531420618"
        base = f"v0:{ts}:{body.decode()}"
        digest = hmac.new(
            secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        good = RawInbound(
            body=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": f"v0={digest}",
            },
        )
        bad = RawInbound(
            body=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": "v0=deadbeef",
            },
        )
        assert adapter.verify(good, conn) is True
        assert adapter.verify(bad, conn) is False


class TestSlackOverlapKey:
    """overlap_key returns the thread_id (conversation-unit granularity)."""

    def test_overlap_key(self) -> None:
        adapter = SlackAdapter()
        event = Message(
            thread_id=ThreadId("slack:C1:1.0"),
            text="hi",
            chat_kind="channel",
        )
        assert adapter.overlap_key(event) == "slack:C1:1.0"
