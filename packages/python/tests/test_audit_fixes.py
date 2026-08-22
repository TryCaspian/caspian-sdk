"""Tests for audit fixes."""

from __future__ import annotations

import pytest

from caspian.core.commands import Post
from caspian.core.overlap import SlotStatus
from caspian.core.ports import AdapterPort, Connection, HostPort, Result, Sent, TransportPort
from caspian.core.types import App, Message, Rule, ThreadId, Overlap, OverlapPolicy
from caspian.core.predicates import MatchAll
from caspian.interpreters.process import ProcessInterpreter
from caspian.facade.thread import Thread, Stream


class MockAdapter(AdapterPort):
    name = "mock"
    def verify(self, raw, conn): return True
    def parse(self, raw): return Result.ok([raw])
    def overlap_key(self, event): return str(event.thread_id)
    def execute(self, cmd, conn): return Result.ok(Sent(message_id="123", raw={"transport": True}))


class MockTransport(TransportPort):
    def __init__(self):
        self.dispatched = []
        
    def dispatch(self, sent):
        self.dispatched.append(sent)
        return Result.ok(Sent(message_id="123", raw=sent.raw))


class MockHost(HostPort):
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def run(self, handler_id, event, skipped_count=0, sink=None):
        self.calls += 1
        if self.fail and self.calls == 1:
            raise ValueError("boom")
        return [Post(thread_id=event.thread_id, text="ok")]


class ConfigurableMockHost(HostPort):
    def __init__(self, fail_on_calls: set[int] | None = None):
        self.fail_on_calls = fail_on_calls or set()
        self.calls = 0

    def run(self, handler_id, event, skipped_count=0, sink=None):
        self.calls += 1
        if self.calls in self.fail_on_calls:
            raise ValueError(f"boom on call {self.calls}")
        return [Post(thread_id=event.thread_id, text=f"ok {event.text}")]


def test_handler_crash_does_not_deadlock_overlap() -> None:
    rule = Rule(
        predicate=MatchAll(),
        handler_id="h1",
        overlap=Overlap(policy=OverlapPolicy.QUEUE)
    )
    app = App(rules=[rule])
    adapter = MockAdapter()
    host = MockHost(fail=True)
    transport = MockTransport()

    interp = ProcessInterpreter(
        app=app,
        adapter=adapter,
        connection=Connection(id="conn-1", channel="mock", config={}),
        host=host,
        transport=transport,
    )

    event1 = Message(thread_id=ThreadId("t:1"), text="1", chat_kind="dm")
    with pytest.raises(ValueError, match="boom"):
        interp.handle_webhook(event1, trusted=True)

    state = interp._state.get_overlap("t:1")
    assert state.status == SlotStatus.IDLE

    event2 = Message(thread_id=ThreadId("t:1"), text="2", chat_kind="dm")
    interp.handle_webhook(event2, trusted=True)
    
    assert host.calls == 2


def test_queued_replay_crash_cleans_up_overlap_state() -> None:
    rule = Rule(
        predicate=MatchAll(),
        handler_id="h1",
        overlap=Overlap(policy=OverlapPolicy.QUEUE)
    )
    app = App(rules=[rule])
    adapter = MockAdapter()
    host = ConfigurableMockHost(fail_on_calls={1})
    transport = MockTransport()

    interp = ProcessInterpreter(
        app=app,
        adapter=adapter,
        connection=Connection(id="conn-1", channel="mock", config={}),
        host=host,
        transport=transport,
    )

    # Put state in busy with 1 queued
    from caspian.core.overlap import OverlapState
    interp._state.set_overlap("t:1", OverlapState(status=SlotStatus.BUSY, queued=1, skipped_count=0))
    interp._pending["t:1"] = Message(thread_id=ThreadId("t:1"), text="queued-msg", chat_kind="dm")

    # Draining when the queued handler raises must not leave state locked
    with pytest.raises(ValueError, match="boom on call 1"):
        interp._drain("t:1", rule)

    # State must be idle now
    state = interp._state.get_overlap("t:1")
    assert state.status == SlotStatus.IDLE


class MockSink:
    def __init__(self, message_id=""):
        self.can_stream = True
        self.message_id = message_id
        
    def emit(self, command):
        return self.message_id


def test_stream_no_double_post_on_fallback() -> None:
    thread = Thread(thread_id=ThreadId("t:1"), sink=MockSink(""))
    stream = thread.stream(min_chars=1, throttle=0)
    
    stream.append("hello")
    assert stream._sent == "hello"
    assert not stream.live
    
    stream.close()
    
    # "hello" was already emitted to sink; no new text appended, so no post command enqueued
    assert len(thread.commands) == 0

    thread2 = Thread(thread_id=ThreadId("t:2"), sink=MockSink(""))
    stream2 = thread2.stream(min_chars=1, throttle=0)
    stream2.append("hello")
    stream2.append(" world")
    stream2.close()
    
    # "hello" was sent to sink; only the unsent suffix " world" should be posted!
    assert len(thread2.commands) == 1
    assert thread2.commands[0].text == " world"


def test_facade_host_async_handler() -> None:
    from caspian.facade.host import FacadeHost

    async def async_handler(thread: Thread, msg: Message, ctx) -> None:
        thread.post(f"async reply: {msg.text}")

    host = FacadeHost({"h_async": async_handler})
    msg = Message(thread_id=ThreadId("t:1"), text="ping", chat_kind="dm")
    cmds = host.run("h_async", msg)
    assert len(cmds) == 1
    assert cmds[0].text == "async reply: ping"


def test_facade_host_async_handler_inside_running_loop() -> None:
    import asyncio
    from caspian.facade.host import FacadeHost

    async def async_handler(thread: Thread, msg: Message, ctx) -> None:
        await asyncio.sleep(0.01)
        thread.post(f"inside loop: {msg.text}")

    async def runner():
        host = FacadeHost({"h_async": async_handler})
        msg = Message(thread_id=ThreadId("t:1"), text="pong", chat_kind="dm")
        return host.run("h_async", msg)

    cmds = asyncio.run(runner())
    assert len(cmds) == 1
    assert cmds[0].text == "inside loop: pong"


