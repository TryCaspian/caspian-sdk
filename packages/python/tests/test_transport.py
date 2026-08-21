"""Tests for the HTTP transport and the Process interpreter's dispatch wiring."""

from __future__ import annotations

from caspian.adapters.telegram import TelegramAdapter
from caspian.core.commands import Post
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import ConnectionId, ThreadId
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

    def test_keeps_json_body_so_poll_can_read_updates(self) -> None:
        """Live getUpdates is useless if dispatch throws the body away."""
        import httpx

        from caspian.interpreters.polling import _extract_updates

        payload = {"ok": True, "result": [{"update_id": 1, "message": {"text": "hi"}}]}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        t = HttpTransport(transport=httpx.MockTransport(handler))
        result = t.dispatch(
            Sent(
                raw={
                    "transport": "http_json",
                    "method": "POST",
                    "url": "https://api.telegram.org/botT/getUpdates",
                    "json": {"offset": 0, "timeout": 0},
                    "native": "getUpdates",
                }
            )
        )
        assert result.is_ok
        assert _extract_updates(result.value) == payload["result"]

    def test_does_not_guess_a_platform_message_id(self) -> None:
        import httpx

        payload = {"ok": True, "result": {"message_id": 42}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        t = HttpTransport(transport=httpx.MockTransport(handler))
        result = t.dispatch(
            Sent(
                raw={
                    "transport": "http_json",
                    "method": "POST",
                    "url": "https://api.telegram.org/botT/sendMessage",
                    "json": {},
                    "native": "sendMessage",
                }
            )
        )
        assert result.is_ok
        assert result.value.message_id == ""
        assert result.value.raw["response"] == payload


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

    def test_adapter_reads_posted_id_after_dispatch(self) -> None:
        class TelegramBody:
            def dispatch(self, sent: Sent) -> Result:
                return Result.ok(
                    Sent(
                        raw={
                            **sent.raw,
                            "response": {"ok": True, "result": {"message_id": 42}},
                        }
                    )
                )

        cx = Caspian()
        interp = ProcessInterpreter(
            cx.app,
            TelegramAdapter(),
            self._conn(),
            transport=TelegramBody(),
        )
        planned = TelegramAdapter().execute(
            Post(thread_id=ThreadId("telegram:555"), text="hi"),
            self._conn(),
        )
        result = interp._maybe_dispatch(planned)
        assert result.is_ok
        assert result.value.message_id == "42"

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
