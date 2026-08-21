"""Tests for the HTTP transport and the Process interpreter's dispatch wiring."""

from __future__ import annotations

from caspian.adapters.telegram import TelegramAdapter
from caspian.core.ports import Connection, RawInbound, Sent
from caspian.core.types import ConnectionId
from caspian.facade.caspian import Caspian
from caspian.facade.host import FacadeHost
from caspian.interpreters import ProcessInterpreter
from caspian.interpreters.transport import HttpTransport, RecordingTransport


class TestHttpTransport:
    def test_noop_transport(self) -> None:
        t = HttpTransport()
        result = t.dispatch(Sent(raw={"transport": "noop", "native": "markRead"}))
        assert result.is_ok

    def test_unsupported_transport(self) -> None:
        t = HttpTransport()
        result = t.dispatch(Sent(raw={"transport": "smtp"}))
        assert not result.is_ok


class TestProcessInterpreterDispatch:
    def _conn(self) -> Connection:
        return Connection(
            id=ConnectionId("c1"), channel="telegram", config={"bot_token": "123:ABC"}
        )

    def test_end_to_end_with_recording_transport(self) -> None:
        cx = Caspian()
        cx.on_message({"channel": "telegram"}, lambda t, m, c: t.post("hello back"))

        transport = RecordingTransport()
        interp = ProcessInterpreter(
            cx.app,
            TelegramAdapter(),
            self._conn(),
            host=FacadeHost(cx._handlers),
            transport=transport,
        )

        update = (
            b'{"message": {"message_id": 1, "from": {"id": 9}, '
            b'"chat": {"id": 555, "type": "private"}, "text": "hi"}}'
        )
        results = interp.handle_webhook(RawInbound(body=update), trusted=True)

        assert all(r.is_ok for r in results)
        # typing + post both dispatched
        natives = [s.raw.get("native") for s in transport.dispatched]
        assert "sendChatAction" in natives
        assert "sendMessage" in natives

    def test_verify_failure_blocks_processing(self) -> None:
        cx = Caspian()
        cx.on_message({"channel": "telegram"}, lambda t, m, c: t.post("x"))
        conn = Connection(
            id=ConnectionId("c1"),
            channel="telegram",
            config={"bot_token": "123:ABC", "webhook_secret": "sekret"},
        )
        interp = ProcessInterpreter(
            cx.app, TelegramAdapter(), conn, host=FacadeHost(cx._handlers)
        )

        raw = RawInbound(
            body=b'{"message": {"chat": {"id": 1, "type": "private"}, "text": "hi"}}',
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        results = interp.handle_webhook(raw)
        assert len(results) == 1 and not results[0].is_ok
