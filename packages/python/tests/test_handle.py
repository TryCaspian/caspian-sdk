"""Tests for the composition root: cx.channels + cx.handle().

A developer should process a webhook with ONE call, no hand-wiring of
adapter + interpreter + transport.
"""

from __future__ import annotations

import json

import pytest

from caspian.facade.caspian import Caspian
from caspian.interpreters.transport import RecordingTransport


def _tg_update(chat_id: int = 555, text: str = "hi") -> bytes:
    return json.dumps(
        {
            "message": {
                "message_id": 1,
                "from": {"id": 9},
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            }
        }
    ).encode()


_TG_SECRET = "s3cr3t"
_TG_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": _TG_SECRET}


class TestChannelManager:
    def test_add_hosted_default(self) -> None:
        cx = Caspian(dispatch=False)
        record = cx.channels.add("email")
        assert record.channel == "email"
        assert "email" in cx.channels.added()
        assert record.inbound_owner == "gateway"

    def test_add_self_host_requires_token(self) -> None:
        from caspian.core.errors import ProvisionError

        cx = Caspian(dispatch=False)
        with pytest.raises(ProvisionError) as exc:
            cx.channels.add("telegram", via="self-host")
        assert exc.value.tag == "ProvisionError"
        assert "bot_token" in exc.value.reason

    def test_unknown_channel_is_hosted_only_not_an_error(self) -> None:
        """The gateway speaks channels this SDK has no adapter for (bluesky,
        zulip, gmeet). Hosted must accept them; only self-host needs an adapter."""
        cx = Caspian(dispatch=False)
        record = cx.channels.add("myspace")  # hosted default
        assert record.inbound_owner == "gateway"

    def test_self_host_unknown_channel_is_provision_error(self) -> None:
        from caspian.core.errors import ProvisionError

        cx = Caspian(dispatch=False)
        with pytest.raises(ProvisionError) as exc:
            cx.channels.add("myspace", via="self-host", bot_token="t")
        assert exc.value.tag == "ProvisionError"
        assert "cannot be self-hosted" in exc.value.reason

    def test_adapter_and_connection_resolved(self) -> None:
        cx = Caspian(dispatch=False)
        cx.channels.add("slack", via="self-host", bot_token="xoxb-1")
        assert cx.channels.adapter_for("slack").name == "slack"
        assert cx.channels.connection_for("slack").config["bot_token"] == "xoxb-1"

    def test_add_gateway_refusal_raises_the_tagged_error(self) -> None:
        from caspian.core.errors import InsufficientCredit
        from caspian.hosted.client import FakeGatewayClient

        client = FakeGatewayClient()
        client.queue_ok({})  # list existing connections
        client.queue_status(402, "no credit")
        cx = Caspian(dispatch=False, gateway_client=client)
        with pytest.raises(InsufficientCredit) as exc:
            cx.channels.add("email")
        assert exc.value.tag == "InsufficientCredit"

    def test_tagged_error_is_raiseable_and_result_shaped(self) -> None:
        from caspian.core.errors import ProvisionError
        from caspian.core.ports import Result

        err = ProvisionError(reason="missing token")
        with pytest.raises(ProvisionError):
            raise err
        result = Result.err(err)
        assert result.error is err
        assert result.error.tag == "ProvisionError"


class TestHandle:
    def _cx(self) -> tuple[Caspian, RecordingTransport]:
        transport = RecordingTransport()
        cx = Caspian(transport=transport)
        cx.channels.add(
            "telegram",
            via="self-host",
            bot_token="123:ABC",
            webhook_url="https://x/y",
            webhook_secret=_TG_SECRET,
        )
        return cx, transport

    def test_handle_drives_full_pipeline(self) -> None:
        cx, transport = self._cx()

        seen = {}

        @cx.on_message({"channel": "telegram"})
        def reply(thread, msg, ctx):
            seen["text"] = msg.text
            thread.post(f"you said: {msg.text}")

        results = cx.handle("telegram", _tg_update(text="ping"), _TG_HEADERS)

        assert seen["text"] == "ping"
        assert all(r.is_ok for r in results)
        natives = [s.raw.get("native") for s in transport.dispatched]
        assert "sendChatAction" in natives  # typing
        assert "sendMessage" in natives  # the reply

    def test_handle_unknown_channel_raises(self) -> None:
        cx, _ = self._cx()
        with pytest.raises(KeyError):
            cx.handle("discord", _tg_update())

    def test_handle_verify_failure_short_circuits(self) -> None:
        transport = RecordingTransport()
        cx = Caspian(transport=transport)
        cx.channels.add(
            "telegram",
            via="self-host",
            bot_token="123:ABC",
            webhook_url="https://x/y",
            webhook_secret="s3cr3t",
        )
        cx.on_message({"channel": "telegram"}, lambda t, m, c: t.post("x"))

        results = cx.handle(
            "telegram", _tg_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}
        )
        assert len(results) == 1 and not results[0].is_ok
        assert transport.dispatched == []

    def test_overlap_state_persists_across_calls(self) -> None:
        transport = RecordingTransport()
        cx = Caspian(transport=transport)
        cx.channels.add(
            "telegram",
            via="self-host",
            bot_token="123:ABC",
            webhook_url="https://x/y",
            webhook_secret=_TG_SECRET,
        )

        # drop policy: while busy, further events are dropped. Since handlers run
        # synchronously the slot frees each call, but the state dict is shared —
        # this asserts the same interpreter (and its StepState) is reused.
        cx.on_message({"channel": "telegram", "overlap": "drop"}, lambda t, m, c: t.post("ok"))

        cx.handle("telegram", _tg_update(), _TG_HEADERS)
        interp_first = cx._interpreters["telegram"]
        cx.handle("telegram", _tg_update(), _TG_HEADERS)
        interp_second = cx._interpreters["telegram"]
        assert interp_first is interp_second

    def test_dispatch_false_returns_request_descriptions(self) -> None:
        cx = Caspian(dispatch=False)
        cx.channels.add(
            "telegram",
            via="self-host",
            bot_token="123:ABC",
            webhook_url="https://x/y",
            webhook_secret=_TG_SECRET,
        )
        cx.on_message({"channel": "telegram"}, lambda t, m, c: t.post("hi"))

        results = cx.handle("telegram", _tg_update(), _TG_HEADERS)
        # No transport: results carry the raw request descriptions from the adapter.
        assert any(
            r.is_ok and isinstance(r.value.raw, dict) and r.value.raw.get("native") == "sendMessage"
            for r in results
        )
