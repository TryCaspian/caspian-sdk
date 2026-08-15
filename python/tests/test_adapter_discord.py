"""Tests for the Discord adapter."""

from __future__ import annotations

import json

from caspian.adapters.discord import DiscordAdapter
from caspian.core.commands import (
    Delete,
    Post,
    React,
    Typing,
)
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import Action, ConnectionId, Message, ThreadId


def _conn(**config: object) -> Connection:
    cfg: dict[str, object] = {"bot_token": "bot.token.abc"}
    cfg.update(config)
    return Connection(id=ConnectionId("c1"), channel="discord", config=cfg)


class TestDiscordParse:
    """adapter.parse turns Discord payloads into kernel Events."""

    def test_parse_ping_returns_empty(self) -> None:
        adapter = DiscordAdapter()
        raw = RawInbound(body=json.dumps({"type": 1}).encode())
        result = adapter.parse(raw)

        assert result.is_ok
        assert result.value == []

    def test_parse_message_component_action(self) -> None:
        adapter = DiscordAdapter()
        payload = {
            "type": 3,
            "id": "int1",
            "token": "tok1",
            "channel_id": "999",
            "data": {"custom_id": "confirm"},
            "message": {"id": "m1"},
            "member": {"user": {"id": "u1"}},
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "action"
        assert events[0].data == "confirm"
        assert events[0].thread_id == "discord:999"
        assert events[0].interaction_id == "int1"

    def test_parse_application_command_message(self) -> None:
        adapter = DiscordAdapter()
        payload = {
            "type": 2,
            "id": "int2",
            "channel_id": "999",
            "data": {"name": "greet", "options": [{"name": "text", "value": "hi"}]},
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "message"
        assert events[0].text == "hi"
        assert events[0].thread_id == "discord:999"

    def test_parse_message_create_gateway(self) -> None:
        adapter = DiscordAdapter()
        payload = {
            "content": "hello there",
            "channel_id": "999",
            "id": "m5",
            "author": {"id": "u9"},
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value[0].kind == "message"
        assert result.value[0].text == "hello there"
        assert result.value[0].sender == "u9"

    def test_parse_reaction_gateway(self) -> None:
        adapter = DiscordAdapter()
        payload = {
            "emoji": {"name": "👍"},
            "channel_id": "999",
            "message_id": "m5",
            "user_id": "u9",
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        assert result.value[0].kind == "reaction"
        assert result.value[0].emoji == "👍"

    def test_parse_unknown_returns_empty(self) -> None:
        adapter = DiscordAdapter()
        raw = RawInbound(body=json.dumps({"type": 99}).encode())
        result = adapter.parse(raw)

        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = DiscordAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestDiscordExecute:
    """adapter.execute turns Commands into Discord API payloads."""

    def test_execute_post(self) -> None:
        adapter = DiscordAdapter()
        cmd = Post(thread_id=ThreadId("discord:999"), text="hi")
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["method"] == "POST"
        assert sent.raw["url"] == "https://discord.com/api/v10/channels/999/messages"
        assert sent.raw["json"]["content"] == "hi"
        assert sent.raw["headers"]["Authorization"] == "Bot bot.token.abc"

    def test_execute_react_put_url(self) -> None:
        adapter = DiscordAdapter()
        cmd = React(
            thread_id=ThreadId("discord:999"), message_id="m1", emoji="👍"
        )
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["method"] == "PUT"
        assert sent.raw["native"] == "react"
        assert "/messages/m1/reactions/" in sent.raw["url"]
        assert sent.raw["url"].endswith("/@me")

    def test_execute_typing(self) -> None:
        adapter = DiscordAdapter()
        cmd = Typing(thread_id=ThreadId("discord:999"))
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["method"] == "POST"
        assert sent.raw["url"].endswith("/channels/999/typing")
        assert sent.raw["native"] == "typing"

    def test_execute_delete(self) -> None:
        adapter = DiscordAdapter()
        cmd = Delete(thread_id=ThreadId("discord:999"), message_id="m1")
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["method"] == "DELETE"
        assert sent.raw["url"].endswith("/channels/999/messages/m1")
        assert "json" not in sent.raw

    def test_execute_without_token_errors(self) -> None:
        adapter = DiscordAdapter()
        cmd = Post(thread_id=ThreadId("discord:999"), text="hi")
        conn = Connection(id=ConnectionId("c1"), channel="discord", config={})
        result = adapter.execute(cmd, conn)

        assert not result.is_ok
        assert result.error is not None
        assert "bot_token" in result.error.reason


class TestDiscordVerifyAck:
    def test_verify_true_when_no_public_key(self) -> None:
        adapter = DiscordAdapter()
        conn = Connection(id=ConnectionId("c1"), channel="discord", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is True

    def test_acknowledge_returns_interaction_callback(self) -> None:
        adapter = DiscordAdapter()
        event = Action(
            thread_id=ThreadId("discord:999"),
            data="confirm",
            interaction_id="int1",
            metadata={"token": "tok1"},
        )
        ack = adapter.acknowledge(event, _conn())

        assert ack is not None and ack.is_ok
        sent = ack.value
        assert "/interactions/int1/tok1/callback" in sent.raw["url"]
        assert sent.raw["json"]["type"] == 6

    def test_acknowledge_none_for_message(self) -> None:
        adapter = DiscordAdapter()
        event = Message(
            thread_id=ThreadId("discord:999"), text="hi", chat_kind="channel"
        )
        assert adapter.acknowledge(event, _conn()) is None


class TestDiscordOverlapKey:
    def test_overlap_key(self) -> None:
        adapter = DiscordAdapter()
        event = Message(
            thread_id=ThreadId("discord:999"), text="hi", chat_kind="channel"
        )
        assert adapter.overlap_key(event) == "discord:999"
