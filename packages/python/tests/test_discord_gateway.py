"""Discord gateway protocol lives on the adapter; SocketSession runs it."""

from __future__ import annotations

import json

import pytest

from caspian.adapters.discord import DiscordAdapter
from caspian.adapters.discord.socket import INTENTS, DiscordSocket
from caspian.core.ports import RawInbound, Result, Sent
from caspian.interpreters.socket import SocketSession


class FakeSocket:
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


class _UrlTransport:
    def dispatch(self, sent: Sent) -> Result:
        return Result.ok(Sent(raw={"response": {"url": "wss://gateway.discord.gg"}}))


def _session(frames: list[dict], sink) -> tuple[SocketSession, FakeSocket, DiscordSocket]:
    socket = FakeSocket(frames)
    driver = DiscordSocket("bot-token")
    session = SocketSession(
        driver,
        sink,
        connect=lambda url, **kw: socket,
        transport=_UrlTransport(),
    )
    return session, socket, driver


HELLO = {"op": 10, "d": {"heartbeat_interval": 45000}}
READY = {
    "op": 0,
    "s": 1,
    "t": "READY",
    "d": {"session_id": "sess-1", "resume_gateway_url": "wss://resume"},
}


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
    session, socket, _ = _session([HELLO, READY, _message("hi")], lambda raw: [])
    await session.run(max_events=1)
    identify = next(f for f in socket.sent if f["op"] == 2)
    assert identify["d"]["token"] == "bot-token"
    assert identify["d"]["intents"] == INTENTS
    assert identify["d"]["intents"] & (1 << 15)


@pytest.mark.asyncio
async def test_forwards_the_inner_payload_not_the_envelope() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    session, _, _ = _session([HELLO, READY, _message("hello there")], sink)
    await session.run(max_events=1)
    assert len(seen) == 1
    assert seen[0]["content"] == "hello there"
    assert "op" not in seen[0] and "t" not in seen[0]


@pytest.mark.asyncio
async def test_message_reaches_the_adapter_as_an_event() -> None:
    adapter = DiscordAdapter()
    events = []

    def sink(raw: RawInbound) -> list[Result]:
        parsed = adapter.parse(raw)
        if parsed.is_ok:
            events.extend(parsed.value)
        return []

    session, _, _ = _session([HELLO, READY, _message("when was Delaware admitted")], sink)
    await session.run(max_events=1)
    assert [e.text for e in events] == ["when was Delaware admitted"]
    assert str(events[0].thread_id) == "discord:chan-9"


@pytest.mark.asyncio
async def test_bot_authored_messages_are_dropped() -> None:
    adapter = DiscordAdapter()
    events = []

    def sink(raw: RawInbound) -> list[Result]:
        parsed = adapter.parse(raw)
        if parsed.is_ok:
            events.extend(parsed.value)
        return []

    session, _, _ = _session([HELLO, READY, _message("my own reply", bot=True)], sink)
    await session.run(max_events=1)
    assert events == []


@pytest.mark.asyncio
async def test_resumes_with_session_after_a_drop() -> None:
    session, socket, _ = _session([HELLO, READY, _message("hi")], lambda raw: [])
    await session.run(max_events=1)
    session._stop = False
    socket._frames = [json.dumps(HELLO), json.dumps(_message("again", mid="m2"))]
    await session.run(max_events=1)
    assert any(f["op"] == 6 for f in socket.sent), "expected a RESUME frame"
    resume = next(f for f in socket.sent if f["op"] == 6)
    assert resume["d"]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_invalid_session_clears_state_so_next_connect_identifies() -> None:
    session, socket, _ = _session(
        [HELLO, READY, {"op": 9, "d": False}, HELLO, _message("hi")],
        lambda raw: [],
    )
    await session.run(max_events=1)
    identifies = [f for f in socket.sent if f.get("op") == 2]
    resumes = [f for f in socket.sent if f.get("op") == 6]
    assert len(identifies) == 2
    assert resumes == []


@pytest.mark.asyncio
async def test_sink_runs_off_the_event_loop() -> None:
    import asyncio
    import threading

    loop_thread = threading.get_ident()
    ran_on: list[int] = []

    def blocking_sink(raw: RawInbound) -> list[Result]:
        ran_on.append(threading.get_ident())
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return []
        raise RuntimeError("handler ran on the event loop")

    session, _, _ = _session([HELLO, READY, _message("hi")], blocking_sink)
    await session.run(max_events=1)
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

    session, _, _ = _session(frames, sink)
    await session.run(max_events=1)
    assert [s["content"] for s in seen] == ["real one"]
