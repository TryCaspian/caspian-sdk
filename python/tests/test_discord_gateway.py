"""Discord Gateway runner: protocol handling, driven by a fake socket.

No network and no websockets package needed: connect and the HTTP gateway
lookup are both injected.
"""

from __future__ import annotations

import json

import pytest

from caspian.adapters.discord import DiscordAdapter
from caspian.core.ports import RawInbound, Result
from caspian.interpreters.discord_gateway import INTENTS, DiscordGatewayRunner


class FakeSocket:
    """Replays a scripted list of frames, recording what was sent."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = [json.dumps(f) for f in frames]
        self.sent: list[dict] = []

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def recv(self) -> str:
        if not self._frames:
            raise ConnectionError("socket drained")
        return self._frames.pop(0)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def _runner(frames: list[dict], sink) -> tuple[DiscordGatewayRunner, FakeSocket]:
    socket = FakeSocket(frames)

    async def http_get(url: str, token: str) -> str:
        return "wss://gateway.discord.gg"

    def connect(url: str, **kwargs: object) -> FakeSocket:
        return socket

    return (
        DiscordGatewayRunner("bot-token", sink, connect=connect, http_get=http_get),
        socket,
    )


HELLO = {"op": 10, "d": {"heartbeat_interval": 45000}}
READY = {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "sess-1", "resume_gateway_url": "wss://resume"}}


def _message(content: str, *, bot: bool = False, mid: str = "m1") -> dict:
    return {
        "op": 0,
        "s": 2,
        "t": "MESSAGE_CREATE",
        "d": {
            "id": mid,
            "channel_id": "chan-9",
            "content": content,
            "author": {"id": "u1", "bot": bot},
        },
    }


@pytest.mark.asyncio
async def test_identifies_with_message_intents() -> None:
    runner, socket = _runner([HELLO, READY, _message("hi")], lambda raw: [])
    await runner.run(max_events=1)
    identify = next(f for f in socket.sent if f["op"] == 2)
    assert identify["d"]["token"] == "bot-token"
    assert identify["d"]["intents"] == INTENTS
    # MESSAGE_CONTENT (1<<15) must be requested or every message arrives empty.
    assert identify["d"]["intents"] & (1 << 15)


@pytest.mark.asyncio
async def test_forwards_the_inner_payload_not_the_envelope() -> None:
    """The adapter parses `d`, so the gateway envelope must be stripped."""
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    runner, _ = _runner([HELLO, READY, _message("hello there")], sink)
    await runner.run(max_events=1)
    assert len(seen) == 1
    assert seen[0]["content"] == "hello there"
    assert "op" not in seen[0] and "t" not in seen[0]


@pytest.mark.asyncio
async def test_message_reaches_the_adapter_as_an_event() -> None:
    """End to end: gateway frame -> sink -> adapter.parse -> kernel Event."""
    adapter = DiscordAdapter()
    events = []

    def sink(raw: RawInbound) -> list[Result]:
        parsed = adapter.parse(raw)
        if parsed.is_ok:
            events.extend(parsed.value)
        return []

    runner, _ = _runner([HELLO, READY, _message("when was Delaware admitted")], sink)
    await runner.run(max_events=1)
    assert [e.text for e in events] == ["when was Delaware admitted"]
    assert str(events[0].thread_id) == "discord:chan-9"


@pytest.mark.asyncio
async def test_bot_authored_messages_are_dropped() -> None:
    """Our own replies echo back; parsing them would loop forever."""
    adapter = DiscordAdapter()
    events = []

    def sink(raw: RawInbound) -> list[Result]:
        parsed = adapter.parse(raw)
        if parsed.is_ok:
            events.extend(parsed.value)
        return []

    runner, _ = _runner([HELLO, READY, _message("my own reply", bot=True)], sink)
    await runner.run(max_events=1)
    assert events == []


@pytest.mark.asyncio
async def test_ready_captures_session_for_resume() -> None:
    runner, _ = _runner([HELLO, READY, _message("hi")], lambda raw: [])
    await runner.run(max_events=1)
    assert runner._session_id == "sess-1"
    assert runner._resume_url == "wss://resume"


@pytest.mark.asyncio
async def test_resumes_with_session_after_a_drop() -> None:
    """Second connection must RESUME, not IDENTIFY, or history is replayed."""
    runner, socket = _runner([HELLO, READY, _message("hi")], lambda raw: [])
    await runner.run(max_events=1)
    runner._stop = False
    socket._frames = [json.dumps(HELLO), json.dumps(_message("again", mid="m2"))]
    await runner.run(max_events=1)
    assert any(f["op"] == 6 for f in socket.sent), "expected a RESUME frame"
    resume = next(f for f in socket.sent if f["op"] == 6)
    assert resume["d"]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_invalid_session_clears_state_so_next_connect_identifies() -> None:
    frames = [HELLO, READY, {"op": 9, "d": False}]
    runner, _ = _runner(frames, lambda raw: [])
    runner._stop = True  # one pass only
    await runner._run_once()
    assert runner._session_id is None
    assert runner._resume_url is None


@pytest.mark.asyncio
async def test_sink_runs_off_the_event_loop() -> None:
    """A blocking handler must not run on the loop.

    Real handlers call synchronous LLM SDKs. asyncio.Runner.run_sync raises
    outright if a loop is already running, and anything merely slow starves the
    heartbeat until Discord drops the connection. Both mean the sink belongs on
    a worker thread.
    """
    import asyncio
    import threading

    loop_thread = threading.get_ident()
    ran_on: list[int] = []

    def blocking_sink(raw: RawInbound) -> list[Result]:
        ran_on.append(threading.get_ident())
        # Mimics Runner.run_sync: explodes if a loop is running on this thread.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return []
        raise RuntimeError("handler ran on the event loop")

    runner, _ = _runner([HELLO, READY, _message("hi")], blocking_sink)
    await runner.run(max_events=1)
    assert ran_on and ran_on[0] != loop_thread


@pytest.mark.asyncio
async def test_unknown_dispatch_types_are_ignored() -> None:
    seen: list[dict] = []
    frames = [
        HELLO,
        READY,
        {"op": 0, "s": 2, "t": "TYPING_START", "d": {"channel_id": "c"}},
        _message("real one"),
    ]

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    runner, _ = _runner(frames, sink)
    await runner.run(max_events=1)
    assert [s["content"] for s in seen] == ["real one"]
