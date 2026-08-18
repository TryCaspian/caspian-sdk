"""Slack Socket Mode runner: protocol handling, driven by a fake socket.

No network and no websockets package: connect and apps.connections.open are
both injected.
"""

from __future__ import annotations

import json
import threading

import pytest

from caspian.adapters.slack import SlackAdapter
from caspian.core.ports import RawInbound, Result
from caspian.interpreters.slack_socket import SlackAuthError, SlackSocketRunner


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


def _runner(frames: list[dict], sink, *, open_fails: str = "") -> tuple[SlackSocketRunner, FakeSocket]:
    socket = FakeSocket(frames)

    async def open_url(token: str) -> str:
        if open_fails:
            raise SlackAuthError(open_fails)
        return "wss://wss-primary.slack.com/link/?ticket=abc"

    def connect(url: str, **kwargs: object) -> FakeSocket:
        return socket

    return SlackSocketRunner("xapp-test", sink, connect=connect, open_url=open_url), socket


@pytest.mark.asyncio
async def test_event_reaches_the_adapter_as_an_event() -> None:
    adapter = SlackAdapter()
    events = []

    def sink(raw: RawInbound) -> list[Result]:
        parsed = adapter.parse(raw)
        if parsed.is_ok:
            events.extend(parsed.value)
        return []

    runner, _ = _runner([HELLO, _event("when was Delaware admitted")], sink)
    await runner.run(max_events=1)
    assert [e.text for e in events] == ["when was Delaware admitted"]


@pytest.mark.asyncio
async def test_envelope_is_acked_before_the_handler_runs() -> None:
    """Slack redelivers anything unacked after ~3s, and handlers call an LLM.

    Acking after the handler means the same message is processed repeatedly,
    which on a slow model shows up as the bot answering three times.
    """
    order: list[str] = []
    socket_ref: list[FakeSocket] = []

    def slow_sink(raw: RawInbound) -> list[Result]:
        order.append("handler")
        return []

    runner, socket = _runner([HELLO, _event("hi")], slow_sink)
    socket_ref.append(socket)

    original_send = socket.send

    async def recording_send(payload: str) -> None:
        order.append("ack")
        await original_send(payload)

    socket.send = recording_send  # type: ignore[method-assign]
    await runner.run(max_events=1)
    assert order == ["ack", "handler"], f"expected ack first, got {order}"
    assert socket.sent == [{"envelope_id": "env-1"}]


@pytest.mark.asyncio
async def test_sink_runs_off_the_event_loop() -> None:
    """Handlers block on an LLM; on the loop that stalls acks and keepalive."""
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

    runner, _ = _runner([HELLO, _event("hi")], sink)
    await runner.run(max_events=1)
    assert ran_on and ran_on[0] != loop_thread


@pytest.mark.asyncio
async def test_hello_is_not_treated_as_an_event() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    runner, socket = _runner([HELLO, _event("real")], sink)
    await runner.run(max_events=1)
    assert len(seen) == 1
    assert seen[0]["event"]["text"] == "real"
    # hello carries no envelope_id, so nothing should have been acked for it
    assert socket.sent == [{"envelope_id": "env-1"}]


@pytest.mark.asyncio
async def test_disconnect_frame_causes_a_reconnect_not_a_crash() -> None:
    """Slack cycles sockets routinely; that must not look like a failure."""
    runner, socket = _runner([HELLO, {"type": "disconnect", "reason": "refresh"}], lambda raw: [])
    runner.stop()  # one pass, then stop so the test terminates
    with pytest.raises(ConnectionError):
        await runner._dispatch(socket, {"type": "disconnect"})


@pytest.mark.asyncio
async def test_bad_app_token_is_fatal_and_does_not_retry() -> None:
    """invalid_auth cannot be fixed by retrying, so the loop must exit."""
    runner, _ = _runner([HELLO], lambda raw: [], open_fails="invalid_auth")
    results = await runner.run(max_events=1)
    assert results == []  # returned rather than spinning forever


@pytest.mark.asyncio
async def test_malformed_frame_does_not_kill_the_socket() -> None:
    seen: list[dict] = []

    def sink(raw: RawInbound) -> list[Result]:
        seen.append(json.loads(raw.body))
        return []

    socket = FakeSocket([HELLO, _event("survived")])
    socket._frames.insert(1, "not json at all")

    async def open_url(token: str) -> str:
        return "wss://x"

    runner = SlackSocketRunner(
        "xapp-test", sink, connect=lambda url, **kw: socket, open_url=open_url
    )
    await runner.run(max_events=1)
    assert [s["event"]["text"] for s in seen] == ["survived"]
