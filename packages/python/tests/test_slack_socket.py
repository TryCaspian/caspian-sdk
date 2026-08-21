"""Slack Socket Mode protocol lives on the adapter; SocketSession runs it."""

from __future__ import annotations

import json
import threading

import pytest

from caspian.adapters.slack import SlackAdapter
from caspian.adapters.slack.socket import SlackSocket
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
            raise ConnectionError("drained")
        return self._frames.pop(0)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


HELLO = {"type": "hello"}


def _event(text: str, *, envelope: str = "env-1") -> dict:
    return {
        "type": "events_api",
        "envelope_id": envelope,
        "payload": {
            "type": "event_callback",
            "event": {
                "type": "message",
                "text": text,
                "channel": "C123",
                "user": "U123",
                "ts": "1700000000.1",
            },
        },
    }


class _UrlTransport:
    def __init__(self, response: dict) -> None:
        self.response = response

    def dispatch(self, sent: Sent) -> Result:
        return Result.ok(Sent(raw={"response": self.response}))


def _session(
    frames: list[object],
    sink,
    *,
    response: dict | None = None,
) -> tuple[SocketSession, FakeSocket]:
    socket = FakeSocket(frames)
    session = SocketSession(
        SlackSocket("xapp-test"),
        sink,
        connect=lambda url, **kw: socket,
        transport=_UrlTransport(response or {"ok": True, "url": "wss://wss-primary.slack.com/link"}),
    )
    return session, socket


@pytest.mark.asyncio
async def test_event_reaches_the_adapter_as_an_event() -> None:
    adapter = SlackAdapter()
    events = []

    def sink(raw: RawInbound) -> list[Result]:
        parsed = adapter.parse(raw)
        if parsed.is_ok:
            events.extend(parsed.value)
        return []

    session, _ = _session([HELLO, _event("when was Delaware admitted")], sink)
    await session.run(max_events=1)
    assert [e.text for e in events] == ["when was Delaware admitted"]


@pytest.mark.asyncio
async def test_envelope_is_acked_before_the_handler_runs() -> None:
    order: list[str] = []

    def slow_sink(raw: RawInbound) -> list[Result]:
        order.append("handler")
        return []

    session, socket = _session([HELLO, _event("hi")], slow_sink)
    original_send = socket.send

    async def recording_send(payload: str) -> None:
        order.append("ack")
        await original_send(payload)

    socket.send = recording_send  # type: ignore[method-assign]
    await session.run(max_events=1)
    assert order == ["ack", "handler"], f"expected ack first, got {order}"
    assert socket.sent == [{"envelope_id": "env-1"}]


@pytest.mark.asyncio
async def test_sink_runs_off_the_event_loop() -> None:
    import asyncio

    loop_thread = threading.get_ident()
    ran_on: list[int] = []

    def sink(raw: RawInbound) -> list[Result]:
        ran_on.append(threading.get_ident())
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return []
        raise RuntimeError("handler ran on the event loop")

    session, _ = _session([HELLO, _event("hi")], sink)
    await session.run(max_events=1)
    assert ran_on and ran_on[0] != loop_thread


@pytest.mark.asyncio
async def test_hello_is_not_treated_as_an_event() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    session, socket = _session([HELLO, _event("real")], sink)
    await session.run(max_events=1)
    assert len(seen) == 1
    assert seen[0]["event"]["text"] == "real"
    assert socket.sent == [{"envelope_id": "env-1"}]


@pytest.mark.asyncio
async def test_disconnect_frame_causes_a_reconnect_not_a_crash() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    session, _ = _session(
        [HELLO, {"type": "disconnect", "reason": "refresh"}, HELLO, _event("after")],
        sink,
    )
    await session.run(max_events=1)
    assert [s["event"]["text"] for s in seen] == ["after"]


@pytest.mark.asyncio
async def test_bad_app_token_is_fatal_and_does_not_retry() -> None:
    session, _ = _session(
        [HELLO], lambda raw: [], response={"ok": False, "error": "invalid_auth"}
    )
    results = await session.run(max_events=1)
    assert results == []


@pytest.mark.asyncio
async def test_malformed_frame_does_not_kill_the_socket() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    session, _ = _session([HELLO, "not json at all", _event("survived")], sink)
    await session.run(max_events=1)
    assert [s["event"]["text"] for s in seen] == ["survived"]
