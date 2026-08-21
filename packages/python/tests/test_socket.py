"""SocketSession — one inbound loop. Drivers supply unwrap; the session does I/O."""

from __future__ import annotations

import json
import threading

import pytest

from caspian.adapters.socket import SocketDecision, SocketUrl
from caspian.core.ports import RawInbound, Result, Sent
from caspian.interpreters.socket import SocketSession


class FakeSocket:
    def __init__(self, frames: list[object]) -> None:
        self._frames = [
            f if isinstance(f, str) else json.dumps(f) for f in frames
        ]
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


class ScriptedDriver:
    def __init__(self, *, url: str = "wss://x", fatal_open: str = "") -> None:
        self.url = url
        self.fatal_open = fatal_open

    def open_plan(self) -> Result:
        return Result.ok(Sent(raw={"transport": "noop", "native": "open"}))

    def url_of(self, sent: Sent) -> SocketUrl:
        if self.fatal_open:
            return SocketUrl(fatal=self.fatal_open)
        return SocketUrl(url=self.url)

    def on_frame(self, frame: dict) -> SocketDecision:
        do = frame.get("do")
        if do == "ack_and_sink":
            return SocketDecision(
                send=('{"ack": true}',),
                sink=RawInbound(body=json.dumps(frame["payload"]).encode()),
            )
        if do == "sink":
            return SocketDecision(
                sink=RawInbound(body=json.dumps(frame["payload"]).encode()),
            )
        if do == "reconnect":
            return SocketDecision(reconnect=True)
        return SocketDecision()

    def heartbeat_payload(self) -> str | None:
        return None

    def connect_kwargs(self) -> dict:
        return {}


class _PassTransport:
    def dispatch(self, sent: Sent) -> Result:
        return Result.ok(sent)


def _session(
    frames: list[object], sink, *, driver: ScriptedDriver | None = None
) -> tuple[SocketSession, FakeSocket]:
    socket = FakeSocket(frames)
    session = SocketSession(
        driver or ScriptedDriver(),
        sink,
        connect=lambda url, **kw: socket,
        transport=_PassTransport(),
    )
    return session, socket


@pytest.mark.asyncio
async def test_send_happens_before_the_sink() -> None:
    order: list[str] = []

    def sink(raw: RawInbound) -> list[Result]:
        order.append("handler")
        return []

    session, socket = _session(
        [{"do": "ack_and_sink", "payload": {"text": "hi"}}], sink
    )
    original = socket.send

    async def recording_send(payload: str) -> None:
        order.append("ack")
        await original(payload)

    socket.send = recording_send  # type: ignore[method-assign]
    await session.run(max_events=1)
    assert order == ["ack", "handler"]
    assert socket.sent == [{"ack": True}]


@pytest.mark.asyncio
async def test_sink_runs_off_the_event_loop() -> None:
    import asyncio

    loop_thread = threading.get_ident()
    ran_on: list[int] = []

    def blocking_sink(raw: RawInbound) -> list[Result]:
        ran_on.append(threading.get_ident())
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return []
        raise RuntimeError("handler ran on the event loop")

    session, _ = _session([{"do": "sink", "payload": {"text": "hi"}}], blocking_sink)
    await session.run(max_events=1)
    assert ran_on and ran_on[0] != loop_thread


@pytest.mark.asyncio
async def test_malformed_frame_does_not_kill_the_socket() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    session, _ = _session(
        ["not json at all", {"do": "sink", "payload": {"ok": True}}], sink
    )
    await session.run(max_events=1)
    assert seen == [{"ok": True}]


@pytest.mark.asyncio
async def test_fatal_open_does_not_retry() -> None:
    session, _ = _session([], lambda raw: [], driver=ScriptedDriver(fatal_open="invalid_auth"))
    results = await session.run(max_events=1)
    assert results == []


@pytest.mark.asyncio
async def test_reconnect_decision_opens_again() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    session, _ = _session(
        [
            {"do": "reconnect"},
            {"do": "sink", "payload": {"n": 2}},
        ],
        sink,
    )
    await session.run(max_events=1)
    assert seen == [{"n": 2}]
