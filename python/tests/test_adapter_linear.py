"""Tests for the Linear adapter."""

from __future__ import annotations

import hashlib
import hmac
import json

from caspian.adapters.linear import LinearAdapter
from caspian.core.commands import Post, Reply
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, Message, ThreadId


def _conn(**config: str) -> Connection:
    return Connection(id=ConnectionId("c1"), channel="linear", config=dict(config))


class TestLinearParse:
    """adapter.parse turns webhook bytes into kernel Events."""

    def test_parse_comment_webhook(self) -> None:
        adapter = LinearAdapter()
        payload = {
            "type": "Comment",
            "action": "create",
            "data": {
                "id": "comment-1",
                "body": "looks good",
                "issue": {"id": "issue-9"},
                "user": {"id": "user-3"},
            },
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        msg = result.value[0]
        assert msg.kind == "message"
        assert msg.text == "looks good"
        assert msg.sender == "user-3"
        assert msg.message_id == "comment-1"
        assert msg.chat_kind == "channel"
        assert msg.thread_id == "linear:issue-9"

    def test_parse_issue_webhook(self) -> None:
        adapter = LinearAdapter()
        payload = {
            "type": "Issue",
            "action": "create",
            "data": {"id": "issue-9", "title": "Fix the bug"},
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        msg = result.value[0]
        assert msg.text == "Fix the bug"
        assert msg.thread_id == "linear:issue-9"

    def test_parse_unknown_type_returns_empty(self) -> None:
        adapter = LinearAdapter()
        payload = {"type": "Reaction", "action": "create", "data": {"id": "r1"}}
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = LinearAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestLinearExecute:
    """adapter.execute turns Commands into GraphQL mutations."""

    def test_execute_post_comment(self) -> None:
        adapter = LinearAdapter()
        cmd = Post(thread_id=ThreadId("linear:issue-9"), text="on it")
        result = adapter.execute(cmd, _conn(api_key="lin_key"))

        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "commentCreate"
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["url"] == "https://api.linear.app/graphql"
        assert sent.raw["headers"]["Authorization"] == "lin_key"
        variables = sent.raw["json"]["variables"]
        assert variables["input"]["issueId"] == "issue-9"
        assert variables["input"]["body"] == "on it"
        assert "commentCreate" in sent.raw["json"]["query"]

    def test_execute_reply_comment(self) -> None:
        adapter = LinearAdapter()
        cmd = Reply(thread_id=ThreadId("linear:issue-9"), reply_to="c1", text="re")
        result = adapter.execute(cmd, _conn(api_key="lin_key"))

        assert result.is_ok
        variables = result.value.raw["json"]["variables"]
        assert variables["input"]["issueId"] == "issue-9"
        assert variables["input"]["body"] == "re"

    def test_execute_without_api_key_errors(self) -> None:
        adapter = LinearAdapter()
        cmd = Post(thread_id=ThreadId("linear:issue-9"), text="on it")
        result = adapter.execute(cmd, _conn())

        assert not result.is_ok
        assert result.error is not None
        assert "api_key" in result.error.reason
        assert result.error.command_tag == "Post"

    def test_execute_unsupported_command_errors(self) -> None:
        from caspian.core.commands import Delete

        adapter = LinearAdapter()
        cmd = Delete(thread_id=ThreadId("linear:issue-9"), message_id="1")
        result = adapter.execute(cmd, _conn(api_key="lin_key"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.command_tag == "Delete"


class TestLinearMisc:
    def test_overlap_key(self) -> None:
        adapter = LinearAdapter()
        event = Message(
            thread_id=ThreadId("linear:issue-9"), text="hi", chat_kind="channel"
        )
        assert adapter.overlap_key(event) == "linear:issue-9"

    def test_capabilities(self) -> None:
        adapter = LinearAdapter()
        expected = frozenset({"receive", "reply", "send", "threading"})
        assert adapter.capabilities() == expected

    def test_verify_true_when_unconfigured(self) -> None:
        adapter = LinearAdapter()
        assert adapter.verify(RawInbound(body=b"{}"), _conn()) is True

    def test_verify_checks_signature(self) -> None:
        adapter = LinearAdapter()
        body = b'{"type":"Comment"}'
        secret = "whsec"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        good = RawInbound(body=body, headers={"Linear-Signature": sig})
        bad = RawInbound(body=body, headers={"Linear-Signature": "nope"})
        conn = _conn(webhook_secret=secret)
        assert adapter.verify(good, conn) is True
        assert adapter.verify(bad, conn) is False
