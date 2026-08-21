"""Foundation tests for hosted mode: GatewayClient classification, outbound, transport."""

from __future__ import annotations

from caspian.core.commands import Post, React, Typing
from caspian.core.ports import Connection
from caspian.core.types import ConnectionId, ThreadId
from caspian.hosted.adapter import GatewayAdapter
from caspian.hosted.client import (
    FakeGatewayClient,
    classify_status,
)
from caspian.hosted.outbound import GatewayOutbound, conversation_id
from caspian.hosted.transport import GatewayTransport


class TestClassifyStatus:
    def test_auth(self) -> None:
        assert classify_status(401).tag == "AuthRequired"
        assert classify_status(403).tag == "AuthRequired"

    def test_credit(self) -> None:
        assert classify_status(402).tag == "InsufficientCredit"

    def test_rate_limited(self) -> None:
        err = classify_status(429, "slow down", retry_after=2.0)
        assert err.tag == "RateLimited"
        assert err.retry_after_seconds == 2.0

    def test_account_required(self) -> None:
        assert classify_status(404, "no account for project").tag == "AccountRequired"

    def test_generic(self) -> None:
        assert classify_status(500, "boom").tag == "GatewayError"


class TestOutbound:
    def _conn(self) -> Connection:
        return Connection(id=ConnectionId("conn_1"), channel="telegram")

    def test_conversation_id_decode(self) -> None:
        assert conversation_id(ThreadId("telegram:conv_abc")) == "conv_abc"

    def test_post_maps_to_messages_endpoint(self) -> None:
        out = GatewayOutbound()
        result = out.execute(Post(thread_id=ThreadId("telegram:conv_abc"), text="hi"), self._conn())
        assert result.is_ok
        raw = result.value.raw
        assert raw["transport"] == "gateway"
        assert raw["gateway"]["path"] == "/v1/conversations/conv_abc/messages"
        assert raw["gateway"]["json_body"]["text"] == "hi"

    def test_react_maps_to_message_react(self) -> None:
        out = GatewayOutbound()
        cmd = React(thread_id=ThreadId("telegram:c"), message_id="m1", emoji="👍")
        result = out.execute(cmd, self._conn())
        assert result.value.raw["gateway"]["path"] == "/v1/messages/m1/react"

    def test_typing_maps(self) -> None:
        out = GatewayOutbound()
        result = out.execute(Typing(thread_id=ThreadId("telegram:c")), self._conn())
        assert result.value.raw["gateway"]["path"] == "/v1/conversations/c/typing"


class TestGatewayTransport:
    def test_dispatch_sends_via_client(self) -> None:
        client = FakeGatewayClient()
        client.queue_ok({"message_id": "srv_1"})
        transport = GatewayTransport(client)

        out = GatewayOutbound()
        sent = out.execute(
            Post(thread_id=ThreadId("telegram:c"), text="hi"),
            Connection(id=ConnectionId("c1"), channel="telegram"),
        ).value

        result = transport.dispatch(sent)
        assert result.is_ok
        assert result.value.message_id == ""
        assert result.value.raw["response"]["message_id"] == "srv_1"
        assert GatewayAdapter().posted_id(result.value) == "srv_1"
        assert len(client.requests) == 1
        assert client.requests[0].path == "/v1/conversations/c/messages"

    def test_dispatch_propagates_error(self) -> None:
        client = FakeGatewayClient()
        client.queue_status(402, "insufficient credit")
        transport = GatewayTransport(client)

        out = GatewayOutbound()
        sent = out.execute(
            Post(thread_id=ThreadId("telegram:c"), text="hi"),
            Connection(id=ConnectionId("c1"), channel="telegram"),
        ).value

        result = transport.dispatch(sent)
        assert not result.is_ok
        assert result.error.tag == "InsufficientCredit"
