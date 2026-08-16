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
    def test_maps_to_message_delete(self) -> None:
        raw = _gw(Delete(thread_id=ThreadId("telegram:c"), message_id="m1"))
        assert raw["native"] == "delete"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/messages/m1/delete"


class TestPin:
    def test_maps_to_message_pin(self) -> None:
        raw = _gw(Pin(thread_id=ThreadId("telegram:c"), message_id="m1"))
        assert raw["native"] == "pin"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/messages/m1/pin"


class TestUnpin:
    def test_maps_to_message_unpin(self) -> None:
        raw = _gw(Unpin(thread_id=ThreadId("telegram:c"), message_id="m1"))
        assert raw["native"] == "unpin"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/messages/m1/unpin"


class TestForward:
    def test_maps_to_message_forward(self) -> None:
        raw = _gw(
            Forward(
                from_thread_id=ThreadId("telegram:src"),
                to_thread_id=ThreadId("telegram:dst"),
                message_id="m1",
            )
        )
        assert raw["native"] == "forward"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/messages/m1/forward"
        assert raw["gateway"]["json_body"]["to"] == "dst"


class TestMarkRead:
    def test_maps_to_conversation_read(self) -> None:
        raw = _gw(MarkRead(thread_id=ThreadId("telegram:c"), message_id="m1"))
        assert raw["native"] == "markRead"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/conversations/c/read"
        assert raw["gateway"]["json_body"]["message_id"] == "m1"


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
    def test_maps_to_interaction_modal(self) -> None:
        raw = _gw(
            OpenModal(
                thread_id=ThreadId("telegram:c"),
                trigger_id="t1",
                blocks=(Block(type="input", content={"id": "name"}),),
                title="My Modal",
                callback_id="cb1",
            )
        )
        assert raw["native"] == "openModal"
        assert raw["gateway"]["method"] == "POST"
        assert raw["gateway"]["path"] == "/v1/interactions/t1/modal"
        body = raw["gateway"]["json_body"]
        assert body["blocks"][0]["type"] == "input"
        assert body["title"] == "My Modal"
        assert body["callback_id"] == "cb1"


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
