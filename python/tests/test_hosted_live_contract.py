"""Contract tests against the REAL gateway response shapes.

These exist because the hosted layer was written against an imagined API and
validated only with FakeGatewayClient, which encoded the same wrong
assumptions. Every fixture below is a verbatim copy of what
api.trycaspianai.com actually returns, so a future drift fails here instead of
silently doing nothing in production.
"""

from __future__ import annotations

import json

from caspian.core.ports import RawInbound
from caspian.hosted.client import GatewayResponse
from caspian.hosted.inbound import GatewayEventParser, GatewayPoller

# Verbatim EventOut from the gateway (serialize.py:message_out + EventOut).
REAL_EVENT = {
    "id": "evt_1", "seq": 24210, "type": "message.received",
    "occurred_at": "2026-08-17T13:18:00Z",
    "data": {
        "customer_id": "cus_1", "agent_id": "agt_1", "connection_id": "conn_1",
        "message": {
            "id": "msg_1", "conversation_id": "conv_1", "connection_id": "conn_1",
            "channel": "discord", "direction": "inbound", "status": "received",
            "sender": {"address": "madmecodes", "name": "Ayush gupta"},
            "recipients": [], "subject": None, "text": "what can u do?",
            "html": None, "media": [], "chat_type": "channel",
            "edited": False, "auto_generated": False,
            "created_at": "2026-08-17T13:18:00",
        },
    },
}


class _Client:
    """Returns exactly what the real endpoint returns: a bare JSON array."""

    def __init__(self, rows):
        self.rows = rows
        self.seen = []

    def send(self, request):
        from caspian.core.ports import Result

        self.seen.append(request)
        return Result.ok(GatewayResponse(status_code=200, json_list=self.rows))


class TestEventShape:
    def test_real_event_envelope_parses(self) -> None:
        r = GatewayEventParser().parse(
            RawInbound(body=json.dumps({"events": [REAL_EVENT]}).encode())
        )
        assert r.is_ok
        assert len(r.value) == 1, "the real gateway envelope must yield one event"
        e = r.value[0]
        assert e.kind == "message"
        assert e.text == "what can u do?"
        assert str(e.thread_id) == "discord:conv_1"

    def test_our_own_outbound_is_not_work(self) -> None:
        """message.sent is the echo of what we just sent. Treating it as inbound
        is how a bot ends up answering itself (see the Aug 15 email loop)."""
        echo = json.loads(json.dumps(REAL_EVENT))
        echo["type"] = "message.sent"
        echo["data"]["message"]["direction"] = "outbound"
        r = GatewayEventParser().parse(
            RawInbound(body=json.dumps({"events": [echo]}).encode())
        )
        assert r.is_ok
        assert r.value == []


class TestPollerContract:
    def test_uses_after_seq_and_limit_not_cursor(self) -> None:
        c = _Client([REAL_EVENT])
        GatewayPoller(c).fetch_raw()
        params = c.seen[0].params
        assert "after_seq" in params, f"real endpoint pages by after_seq: {params}"
        assert "limit" in params
        assert "cursor" not in params

    def test_array_response_is_not_dropped(self) -> None:
        c = _Client([REAL_EVENT])
        poller = GatewayPoller(c)
        raw = poller.fetch_raw()
        assert raw.is_ok
        body = json.loads(raw.value.body)
        assert len(body["events"]) == 1, "a JSON array body must survive decoding"

    def test_cursor_advances_to_highest_seq(self) -> None:
        c = _Client([REAL_EVENT])
        poller = GatewayPoller(c)
        poller.fetch_raw()
        assert poller.cursor == 24210
        poller.fetch_raw()
        assert c.seen[1].params["after_seq"] == "24210", "must not re-read old rows"


class TestTypingUsesMessageEndpoint:
    """Typing hangs off a MESSAGE on the gateway, not a conversation.

    Regression: the mapper posted to /v1/conversations/{cid}/typing, which 404s
    (verified live), so the indicator never appeared in Slack or Discord even
    though step() emitted the command.
    """

    def _adapter_after_inbound(self):
        from caspian.hosted.adapter import GatewayAdapter

        a = GatewayAdapter()
        a.parse(RawInbound(body=json.dumps({"events": [REAL_EVENT]}).encode()))
        return a

    def test_typing_targets_the_inbound_message(self) -> None:
        from caspian.core.commands import Typing
        from caspian.core.ports import Connection
        from caspian.core.types import ConnectionId, ThreadId

        a = self._adapter_after_inbound()
        conn = Connection(id=ConnectionId("c"), channel="gateway", config={})
        r = a.execute(Typing(thread_id=ThreadId("discord:conv_1")), conn)
        assert r.is_ok
        assert r.value.raw["path"] == "/v1/messages/msg_1/typing"
        assert "conversations" not in r.value.raw["path"]

    def test_typing_without_a_known_message_is_a_noop_not_an_error(self) -> None:
        from caspian.core.commands import Typing
        from caspian.core.ports import Connection
        from caspian.core.types import ConnectionId, ThreadId
        from caspian.hosted.adapter import GatewayAdapter

        conn = Connection(id=ConnectionId("c"), channel="gateway", config={})
        r = GatewayAdapter().execute(Typing(thread_id=ThreadId("discord:nope")), conn)
        assert r.is_ok, "a missing indicator must never fail the reply that follows"
        assert "noop" in r.value.raw

    def test_capabilities_do_not_claim_missing_endpoints(self) -> None:
        """The gateway has no pin/unpin/forward/delete/modal routes."""
        from caspian.hosted.adapter import GatewayAdapter

        caps = GatewayAdapter().capabilities()
        assert "delete" not in caps
