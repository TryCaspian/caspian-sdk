"""Outbound mapping tests for the remaining hosted commands → gateway /v1 API."""

from __future__ import annotations

from caspian.core.commands import (
    Delete,
    Forward,
    ListHistory,
    MarkRead,
    OpenModal,
    Pin,
    ScheduleSend,
    SendBlocks,
    Unpin,
)
from caspian.core.ports import Connection
from caspian.core.types import Block, Button, ConnectionId, ThreadId
from caspian.hosted.outbound import GatewayOutbound


def _conn() -> Connection:
    return Connection(id=ConnectionId("c1"), channel="telegram")


def _gw(cmd: object) -> dict[str, object]:
    result = GatewayOutbound().execute(cmd, _conn())  # type: ignore[arg-type]
    assert result.is_ok
    raw = result.value.raw
    assert raw["transport"] == "gateway"
    return raw


class TestDelete:
    """Delete has no gateway endpoint; hosted mode must refuse it clearly."""

    def test_unsupported_in_hosted_mode(self) -> None:
        cmd = Delete(thread_id=ThreadId("telegram:c"), message_id="m1")
        result = GatewayOutbound().execute(cmd, _conn())
        assert not result.is_ok
        assert "not available in hosted mode" in result.error.reason
        assert result.error.command_tag == "Delete"


class TestPin:
    """Pin has no gateway endpoint; hosted mode must refuse it clearly."""

    def test_unsupported_in_hosted_mode(self) -> None:
        cmd = Pin(thread_id=ThreadId("telegram:c"), message_id="m1")
        result = GatewayOutbound().execute(cmd, _conn())
        assert not result.is_ok
        assert "not available in hosted mode" in result.error.reason
        assert result.error.command_tag == "Pin"


class TestUnpin:
    """Unpin has no gateway endpoint; hosted mode must refuse it clearly."""

    def test_unsupported_in_hosted_mode(self) -> None:
        cmd = Unpin(thread_id=ThreadId("telegram:c"), message_id="m1")
        result = GatewayOutbound().execute(cmd, _conn())
        assert not result.is_ok
        assert "not available in hosted mode" in result.error.reason
        assert result.error.command_tag == "Unpin"


class TestForward:
    """Forward has no gateway endpoint; hosted mode must refuse it clearly."""

    def test_unsupported_in_hosted_mode(self) -> None:
        cmd = Forward(
            from_thread_id=ThreadId("telegram:src"),
            to_thread_id=ThreadId("telegram:dst"),
            message_id="m1",
        )
        result = GatewayOutbound().execute(cmd, _conn())
        assert not result.is_ok
        assert "not available in hosted mode" in result.error.reason
        assert result.error.command_tag == "Forward"


class TestMarkRead:
    """MarkRead has no gateway endpoint; hosted mode must refuse it clearly."""

    def test_unsupported_in_hosted_mode(self) -> None:
        cmd = MarkRead(thread_id=ThreadId("telegram:c"), message_id="m1")
        result = GatewayOutbound().execute(cmd, _conn())
        assert not result.is_ok
        assert "not available in hosted mode" in result.error.reason
        assert result.error.command_tag == "MarkRead"


class TestScheduleSend:
    def test_maps_to_conversation_messages(self) -> None:
        raw = _gw(
            ScheduleSend(
                thread_id=ThreadId("telegram:c"),
                text="later",
                send_at=1234,
                actions=(Button(label="Go", data="go"),),
            )
        )
        assert raw["native"] == "schedule"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/conversations/c/messages"
        body = raw["gateway"]["json_body"]
        assert body["text"] == "later"
        assert body["send_at"] == 1234
        assert body["actions"][0]["label"] == "Go"


class TestSendBlocks:
    def test_maps_to_conversation_messages_with_blocks(self) -> None:
        raw = _gw(
            SendBlocks(
                thread_id=ThreadId("telegram:c"),
                blocks=(Block(type="section", content={"text": "hi"}),),
                text="fallback",
                actions=(Button(label="Go", data="go"),),
            )
        )
        assert raw["native"] == "sendBlocks"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/conversations/c/messages"
        body = raw["gateway"]["json_body"]
        assert body["text"] == "fallback"
        assert body["blocks"][0]["type"] == "section"
        assert body["blocks"][0]["content"] == {"text": "hi"}
        assert body["actions"][0]["label"] == "Go"


class TestOpenModal:
    """OpenModal has no gateway endpoint; hosted mode must refuse it clearly."""

    def test_unsupported_in_hosted_mode(self) -> None:
        cmd = OpenModal(
            thread_id=ThreadId("telegram:c"),
            trigger_id="t1",
            blocks=(Block(type="input", content={"id": "name"}),),
            title="My Modal",
            callback_id="cb1",
        )
        result = GatewayOutbound().execute(cmd, _conn())
        assert not result.is_ok
        assert "not available in hosted mode" in result.error.reason
        assert result.error.command_tag == "OpenModal"


class TestListHistory:
    def test_maps_to_backfill_get(self) -> None:
        raw = _gw(
            ListHistory(thread_id=ThreadId("telegram:c"), limit=50, before="m9")
        )
        assert raw["native"] == "listHistory"
        assert raw["gateway"]["method"] == "GET"
        assert raw["gateway"]["path"] == "/v1/conversations/c/backfill"
        params = raw["gateway"]["params"]
        assert params["limit"] == "50"
        assert params["before"] == "m9"
