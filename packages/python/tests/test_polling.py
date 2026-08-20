"""Tests for the self-host polling runner and Telegram update fetching.

No network: a FakeTransport returns a canned getUpdates response, so the whole
poll pipeline is exercised with injected fakes.
"""

from __future__ import annotations

import json
from typing import Any

from caspian.adapters.telegram import TelegramAdapter
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import ConnectionId
from caspian.facade.caspian import Caspian
from caspian.facade.host import FacadeHost
from caspian.interpreters import ProcessInterpreter
from caspian.interpreters.polling import PollingRunner, fetch_updates
from caspian.interpreters.transport import RecordingTransport


def _conn() -> Connection:
    return Connection(
        id=ConnectionId("c1"), channel="telegram", config={"bot_token": "123:ABC"}
    )


def _tg_update(update_id: int, text: str, chat_id: int = 555) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": 9},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _get_updates_response() -> dict[str, Any]:
    # Distinct chats so neither event is suppressed by same-thread overlap.
    return {
        "ok": True,
        "result": [
            _tg_update(10, "hi", chat_id=555),
            _tg_update(11, "yo", chat_id=556),
        ],
    }


class FakeTransport:
    """Records dispatched requests, returns a canned getUpdates body."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.dispatched: list[Sent] = []
        self._response = response

    def dispatch(self, sent: Sent) -> Result:
        self.dispatched.append(sent)
        return Result.ok(Sent(raw={"body": json.dumps(self._response).encode()}))


class TestFetchUpdates:
    def test_returns_two_updates_and_next_offset(self) -> None:
        transport = FakeTransport(_get_updates_response())
        result = fetch_updates(TelegramAdapter(), _conn(), 0, transport)

        assert result.is_ok
        updates, next_offset = result.value
        assert len(updates) == 2
        assert next_offset == 12  # max update_id (11) + 1

    def test_dispatches_a_getupdates_request(self) -> None:
        transport = FakeTransport(_get_updates_response())
        fetch_updates(TelegramAdapter(), _conn(), 7, transport)

        assert len(transport.dispatched) == 1
        req = transport.dispatched[0].raw
        assert req["native"] == "getUpdates"
        assert req["json"] == {"offset": 7, "timeout": 0}

    def test_missing_token_returns_error(self) -> None:
        conn = Connection(id=ConnectionId("c1"), channel="telegram", config={})
        transport = FakeTransport(_get_updates_response())
        result = fetch_updates(TelegramAdapter(), conn, 0, transport)

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"

    def test_adapter_without_poll_errors(self) -> None:
        class NoPoll:
            name = "nopoll"

        transport = FakeTransport(_get_updates_response())
        result = fetch_updates(NoPoll(), _conn(), 0, transport)  # type: ignore[arg-type]

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"


class TestPollingRunner:
    def test_poll_once_feeds_each_update_to_sink(self) -> None:
        transport = FakeTransport(_get_updates_response())
        seen: list[RawInbound] = []

        def sink(raw: RawInbound) -> list[Result]:
            seen.append(raw)
            return [Result.ok(Sent(raw={"native": "noop"}))]

        runner = PollingRunner(
            TelegramAdapter(), _conn(), sink, transport=transport, sleep=lambda _: None
        )
        results = runner.poll_once()

        assert len(seen) == 2
        assert len(results) == 2
        assert json.loads(seen[0].body)["update_id"] == 10
        assert json.loads(seen[1].body)["update_id"] == 11

    def test_poll_once_drives_process_interpreter(self) -> None:
        cx = Caspian()
        cx.on_message(
            {"channel": "telegram"}, lambda t, m, c: t.post(f"echo: {m.text}")
        )
        rec = RecordingTransport()
        interp = ProcessInterpreter(
            cx.app,
            TelegramAdapter(),
            _conn(),
            host=FacadeHost(cx._handlers),
            transport=rec,
        )
        transport = FakeTransport(_get_updates_response())
        runner = PollingRunner(
            TelegramAdapter(),
            _conn(),
            interp.handle_webhook,
            transport=transport,
            sleep=lambda _: None,
        )

        results = runner.poll_once()

        assert results and all(r.is_ok for r in results)
        natives = [s.raw.get("native") for s in rec.dispatched]
        assert natives.count("sendMessage") == 2

    def test_run_forever_stops_after_max_iterations(self) -> None:
        transport = FakeTransport(_get_updates_response())
        sleeps: list[float] = []
        seen: list[RawInbound] = []

        def sink(raw: RawInbound) -> list[Result]:
            seen.append(raw)
            return [Result.ok(Sent())]

        runner = PollingRunner(
            TelegramAdapter(),
            _conn(),
            sink,
            transport=transport,
            sleep=sleeps.append,
        )
        results = runner.run_forever(max_iterations=1, sleep=0.0)

        assert len(seen) == 2  # exactly one poll iteration
        assert len(results) == 2
        assert sleeps == []  # loop breaks before sleeping

    def test_poll_once_no_poll_returns_single_error(self) -> None:
        class NoPoll:
            name = "nopoll"

        transport = FakeTransport(_get_updates_response())
        runner = PollingRunner(
            NoPoll(),  # type: ignore[arg-type]
            _conn(),
            lambda raw: [],
            transport=transport,
        )
        results = runner.poll_once()

        assert len(results) == 1
        assert not results[0].is_ok
