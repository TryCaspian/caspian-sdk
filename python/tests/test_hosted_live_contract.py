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
        # The real endpoint filters by after_seq; a fake that ignores it would
        # hide exactly the replay bug these tests exist to catch.
        after = int(request.params.get("after_seq", "0") or 0)
        rows = [r for r in self.rows if int(r.get("seq", 0)) > after]
        return Result.ok(GatewayResponse(status_code=200, json_list=rows))


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
        GatewayPoller(c, replay=True).fetch_raw()
        params = c.seen[0].params
        assert "after_seq" in params, f"real endpoint pages by after_seq: {params}"
        assert "limit" in params
        assert "cursor" not in params

    def test_array_response_is_not_dropped(self) -> None:
        c = _Client([REAL_EVENT])
        poller = GatewayPoller(c, replay=True)
        raw = poller.fetch_raw()
        assert raw.is_ok
        body = json.loads(raw.value.body)
        assert len(body["events"]) == 1, "a JSON array body must survive decoding"

    def test_cursor_advances_to_highest_seq(self) -> None:
        c = _Client([REAL_EVENT])
        poller = GatewayPoller(c, replay=True)
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
        # GatewayTransport reads raw["gateway"]["path"]; asserting the flat key
        # is how the first attempt at this fix passed while sending an empty path.
        gw = r.value.raw["gateway"]
        assert gw["path"] == "/v1/messages/msg_1/typing"
        assert gw["method"] == "POST"
        assert "conversations" not in gw["path"]

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


class TestNoHistoryReplayOnStart:
    """A restart must not re-answer every message ever received.

    Regression: the poller began at after_seq=0, so restarting the bot replayed
    the whole history. Users saw the bot reply on a channel they had not pinged
    and answer stale questions.
    """

    def test_fresh_poller_skips_existing_history(self) -> None:
        c = _Client([REAL_EVENT])
        poller = GatewayPoller(c)
        raw = poller.fetch_raw()
        assert raw.is_ok
        assert json.loads(raw.value.body)["events"] == [], "history must not be replayed"
        assert poller.cursor == 24210, "but the cursor must move past it"

    def test_replay_is_opt_in(self) -> None:
        c = _Client([REAL_EVENT])
        raw = GatewayPoller(c, replay=True).fetch_raw()
        assert len(json.loads(raw.value.body)["events"]) == 1

    def test_explicit_cursor_is_respected(self) -> None:
        c = _Client([REAL_EVENT])
        raw = GatewayPoller(c, cursor="24000").fetch_raw()
        assert len(json.loads(raw.value.body)["events"]) == 1


class TestTypingIsSentBeforeTheHandler:
    """The indicator exists to show WHILE the agent thinks."""

    def test_typing_dispatches_before_handler_output(self) -> None:
        from caspian import Caspian
        from caspian.interpreters.transport import RecordingTransport

        rec = RecordingTransport()
        cx = Caspian(transport=rec)
        cx.channels.add("telegram", via="self-host", bot_token="1:A")

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx):  # noqa: ANN001
            # Whatever the handler did must land AFTER the indicator.
            thread.post("done")

        update = json.dumps({"update_id": 1, "message": {
            "message_id": 1, "from": {"id": 1},
            "chat": {"id": 5, "type": "private"}, "text": "hi"}}).encode()
        cx.handle("telegram", update)

        order = [s.raw.get("url", "").rsplit("/", 1)[-1] for s in rec.dispatched]
        assert order, "nothing was dispatched"
        assert "sendChatAction" in order[0], f"typing must be first, got {order}"


class TestPostThreadsTheConversation:
    """A handler answering an inbound message means "reply to this".

    Regression: thread.post() mapped to a proactive send, which on email omits
    In-Reply-To/References and the Re: subject, so Gmail showed a stray new
    message instead of a threaded conversation.
    """

    def _adapter(self):
        from caspian.hosted.adapter import GatewayAdapter

        a = GatewayAdapter()
        a.parse(RawInbound(body=json.dumps({"events": [REAL_EVENT]}).encode()))
        return a

    def _conn(self):
        from caspian.core.ports import Connection
        from caspian.core.types import ConnectionId

        return Connection(id=ConnectionId("c"), channel="gateway", config={})

    def test_post_becomes_a_reply_to_the_trigger(self) -> None:
        from caspian.core.commands import Post
        from caspian.core.types import ThreadId

        r = self._adapter().execute(
            Post(thread_id=ThreadId("discord:conv_1"), text="hi"), self._conn()
        )
        assert r.is_ok
        assert r.value.raw["gateway"]["path"] == "/v1/messages/msg_1/reply"

    def test_standalone_post_stays_a_plain_send(self) -> None:
        from caspian.core.commands import Post
        from caspian.core.types import ThreadId

        r = self._adapter().execute(
            Post(thread_id=ThreadId("discord:conv_1"), text="hi", standalone=True),
            self._conn(),
        )
        assert r.is_ok
        assert r.value.raw["gateway"]["path"] == "/v1/conversations/conv_1/messages"

    def test_post_with_no_trigger_is_a_plain_send(self) -> None:
        from caspian.core.commands import Post
        from caspian.core.types import ThreadId
        from caspian.hosted.adapter import GatewayAdapter

        r = GatewayAdapter().execute(
            Post(thread_id=ThreadId("discord:unknown"), text="hi"), self._conn()
        )
        assert r.is_ok
        assert "conversations" in r.value.raw["gateway"]["path"]

    def test_thread_send_is_explicitly_standalone(self) -> None:
        from caspian.core.types import ThreadId
        from caspian.facade.thread import Thread

        t = Thread(thread_id=ThreadId("discord:conv_1"))
        t.send("unprompted")
        assert t.commands[0].standalone is True
        t2 = Thread(thread_id=ThreadId("discord:conv_1"))
        t2.post("answer")
        assert t2.commands[0].standalone is False


class TestInstantAck:
    """An ack answers the silence on channels with no typing indicator.

    Email, SMS and X have no 'thinking' signal, so without this a human waits on
    nothing while the agent works. The old SDK spelled it listen(ack="...").
    """

    def _cx(self):
        from caspian import Caspian
        from caspian.interpreters.transport import RecordingTransport

        rec = RecordingTransport()
        cx = Caspian(transport=rec)
        cx.channels.add("telegram", via="self-host", bot_token="1:A")
        return cx, rec

    def _update(self) -> bytes:
        return json.dumps({"update_id": 1, "message": {
            "message_id": 1, "from": {"id": 1},
            "chat": {"id": 5, "type": "private"}, "text": "hi"}}).encode()

    def test_ack_is_sent_before_the_handler_runs(self) -> None:
        cx, rec = self._cx()
        order: list[str] = []

        @cx.on_message({"channel": "telegram", "ack": "On it, one moment…"})
        def handler(thread, msg, ctx):  # noqa: ANN001
            order.append("handler")
            thread.post("the real answer")

        cx.handle("telegram", self._update())
        texts = [s.raw.get("json", {}).get("text") for s in rec.dispatched]
        sent = [t for t in texts if t]
        assert sent[0] == "On it, one moment…", f"ack must be first, got {sent}"
        assert "the real answer" in sent
        assert order == ["handler"]

    def test_no_ack_by_default(self) -> None:
        cx, rec = self._cx()

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx):  # noqa: ANN001
            thread.post("answer")

        cx.handle("telegram", self._update())
        texts = [s.raw.get("json", {}).get("text") for s in rec.dispatched if s.raw.get("json")]
        assert [t for t in texts if t] == ["answer"]

    def test_ack_lives_on_the_rule_as_data(self) -> None:
        cx, _ = self._cx()

        @cx.on_message({"channel": "telegram", "ack": "hold on"})
        def handler(thread, msg, ctx):  # noqa: ANN001
            pass

        assert cx.app.rules[0].ack == "hold on"
