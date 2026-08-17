"""Caspian — the B-surface facade. What bot developers import and write against.

cx.on_message({...}, handler) builds Rules. The App is inspectable data.
Self-host, poll, and hosted all run the same App through ProcessInterpreter.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol

from caspian.core.errors import AuthRequired, ProvisionError
from caspian.core.interpreter_memory import MemoryInterpreter
from caspian.core.ports import RawInbound, Result, Sent
from caspian.core.predicates import (
    And,
    MatchChannel,
    MatchChatKind,
    MatchKind,
    Predicate,
)
from caspian.core.types import (
    App,
    ConnectionId,
    Overlap,
    OverlapPolicy,
    Rule,
)
from caspian.facade.channels import ChannelManager
from caspian.facade.host import FacadeHost, HandlerContext
from caspian.interpreters.process import ProcessInterpreter

Handler = Callable[..., Any]


class Transport(Protocol):
    def dispatch(self, sent: Sent) -> Result: ...


class Caspian:
    """The public SDK entry point. Builds an App of Rules from on_message/on_action calls.

    The App is pure data — inspectable, serializable, testable without a network.

    Self-host::

        cx = Caspian()
        cx.channels.add("telegram", via="self-host", bot_token=TG, webhook_url=URL)

        @cx.on_message({"channel": "telegram"})
        def reply(thread, msg, ctx):
            thread.post(f"you said: {msg.text}")

        results = cx.handle("telegram", request_body, request_headers)
        # or, no public URL:
        cx.poll("telegram")

    Hosted (Telegram still needs a BotFather token)::

        cx = Caspian(api_key=KEY)
        cx.channels.add("telegram", bot_token=TG)
        cx.handle("gateway", body, headers)  # or cx.run()
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        webhook_secret: str = "",
        gateway_client: Any = None,  # noqa: ANN401
        transport: Transport | None = None,
        dispatch: bool = True,
    ) -> None:
        self._rules: list[Rule] = []
        self._handlers: dict[str, Handler] = {}
        self._host = FacadeHost(self._handlers)
        self._dispatch = dispatch
        self._webhook_secret = webhook_secret
        self._gateway_client = gateway_client
        if self._gateway_client is None and api_key:
            from caspian.hosted.client import HttpGatewayClient

            self._gateway_client = HttpGatewayClient(api_key=api_key, base_url=base_url)
        self.channels = ChannelManager(gateway_client=self._gateway_client)
        self._interpreters: dict[str, ProcessInterpreter] = {}
        self._transport = transport
        if dispatch and transport is None:
            self._transport = self._default_transport()

    def _default_transport(self) -> Transport:
        from caspian.hosted.transport import GatewayTransport
        from caspian.interpreters.smtp import SmtpTransport
        from caspian.interpreters.transport import HttpTransport, MultiplexTransport
        from caspian.interpreters.voice import VoiceResponder

        http = HttpTransport()
        routes: dict[str, Any] = {
            "http_json": http,
            "http_form": http,
            "http_multipart": http,
            "noop": http,
            "smtp": SmtpTransport(),
            "twiml": VoiceResponder(),
        }
        if self._gateway_client is not None:
            routes["gateway"] = GatewayTransport(self._gateway_client)
        return MultiplexTransport(routes)

    @property
    def app(self) -> App:
        """The current program as inspectable data."""
        return App(rules=tuple(self._rules))

    def handle(
        self,
        channel: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> list[Result]:
        """Composition root: drive one raw inbound through the full pipeline.

        verify → parse → step → host → execute → transport.

        `channel="gateway"` is the hosted inbound owner. A hosted platform
        channel (inbound_owner=gateway) cannot be consumed via handle(channel).
        """
        if channel != "gateway":
            owner = self.channels.inbound_owner(channel)
            if owner != "local":
                return [
                    Result.err(
                        ProvisionError(
                            reason=(
                                f"Inbound for {channel!r} is owned by the gateway; "
                                "use handle('gateway', ...) or run()"
                            )
                        )
                    )
                ]
        interp = self._interpreter_for(channel)
        return interp.handle_webhook(RawInbound(body=body, headers=headers or {}))

    def poll(
        self,
        channel: str,
        *,
        transport: Transport | None = None,
        max_iterations: int | None = None,
        offset: int = 0,
    ) -> list[Result]:
        """Self-host long-poll. Each update is fed to the same handle_webhook."""
        owner = self.channels.inbound_owner(channel)
        if owner != "local":
            return [
                Result.err(
                    ProvisionError(
                        reason=f"Inbound for {channel!r} is owned by the gateway; use run()"
                    )
                )
            ]
        from caspian.interpreters.polling import PollingRunner

        interp = self._interpreter_for(channel)
        runner = PollingRunner(
            self.channels.adapter_for(channel),
            self.channels.connection_for(channel),
            interp.handle_webhook,
            transport=transport or self._transport,  # type: ignore[arg-type]
            offset=offset,
            sleep=lambda _s: None,
        )
        return runner.run_forever(max_iterations=max_iterations)

    def run(
        self, *, max_iterations: int | None = None, interval: float = 1.0
    ) -> list[Result]:
        """Hosted poll loop: GET /v1/events → handle('gateway', ...).

        Waits `interval` seconds between polls. Without it this spins as fast as
        the network allows, which hammers the gateway for no benefit.
        """
        if self._gateway_client is None:
            return [
                Result.err(
                    AuthRequired(reason="hosted run() requires api_key or gateway_client")
                )
            ]
        from caspian.hosted.inbound import GatewayPoller

        poller = GatewayPoller(self._gateway_client)
        collected: list[Result] = []
        iterations = 0
        while True:
            fetched = poller.fetch_raw()
            if not fetched.is_ok:
                collected.append(fetched)
            else:
                raw: RawInbound = fetched.value
                collected.extend(self.handle("gateway", raw.body, raw.headers))
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if interval > 0:
                import time

                time.sleep(interval)
        return collected

    def _interpreter_for(self, channel: str) -> ProcessInterpreter:
        """Get-or-create the ProcessInterpreter (preserves overlap state)."""
        if channel not in self._interpreters:
            if channel == "gateway":
                self._interpreters[channel] = self._gateway_interpreter()
            else:
                self._interpreters[channel] = ProcessInterpreter(
                    self.app,
                    self.channels.adapter_for(channel),
                    self.channels.connection_for(channel),
                    host=self._host,
                    transport=self._transport if self._dispatch else None,
                )
        # Rules may have been added after the first handle(); keep the runner current.
        self._interpreters[channel]._app = self.app
        return self._interpreters[channel]

    def _gateway_interpreter(self) -> ProcessInterpreter:
        from caspian.core.ports import Connection
        from caspian.hosted.adapter import GatewayAdapter

        adapter = GatewayAdapter(webhook_secret=self._webhook_secret)
        connection = Connection(
            id=ConnectionId("gateway:0"),
            channel="gateway",
            config={},
        )
        return ProcessInterpreter(
            self.app,
            adapter,
            connection,
            host=self._host,
            transport=self._transport if self._dispatch else None,
        )

    def on_message(
        self,
        options: dict[str, Any] | None = None,
        handler: Handler | None = None,
    ) -> Callable[[Handler], Handler] | None:
        """Register a message handler. Can be used as a decorator or called directly.

        Options:
            channel: str | list[str] — filter by channel name(s)
            kind: "dm" | "group" | "channel" — filter by chat kind
            overlap: "queue" | "debounce" | "drop" | "parallel" | "stream"
            bound: int — overlap queue bound (default 16)
        """
        if handler is not None:
            self._register_message_handler(options or {}, handler)
            return None

        def decorator(fn: Handler) -> Handler:
            self._register_message_handler(options or {}, fn)
            return fn

        return decorator

    def on_action(
        self,
        options: dict[str, Any] | None = None,
        handler: Handler | None = None,
    ) -> Callable[[Handler], Handler] | None:
        """Register an action (button/callback) handler."""
        if handler is not None:
            self._register_action_handler(options or {}, handler)
            return None

        def decorator(fn: Handler) -> Handler:
            self._register_action_handler(options or {}, fn)
            return fn

        return decorator

    def use(self, rule: Rule) -> None:
        """Power-user escape: add a raw Rule directly (A-level API)."""
        self._rules.append(rule)

    def memory(self) -> MemoryInterpreter:
        """Create a MemoryInterpreter for testing this app."""
        interp = MemoryInterpreter()
        for hid, fn in self._handlers.items():
            interp.register_handler(hid, fn)
        return interp

    # ─── Internal ────────────────────────────────────────────────────────────

    def _register_message_handler(self, options: dict[str, Any], fn: Handler) -> None:
        handler_id = f"handler_{uuid.uuid4().hex[:8]}"
        self._handlers[handler_id] = fn

        pred: Predicate = MatchKind(kind="message")
        pred = self._apply_filters(pred, options)

        overlap = self._build_overlap(options)
        self._rules.append(
            Rule(
                predicate=pred,
                overlap=overlap,
                handler_id=handler_id,
                ack=str(options.get("ack", "")),
            )
        )

    def _register_action_handler(self, options: dict[str, Any], fn: Handler) -> None:
        handler_id = f"handler_{uuid.uuid4().hex[:8]}"
        self._handlers[handler_id] = fn

        pred: Predicate = MatchKind(kind="action")
        pred = self._apply_filters(pred, options)

        overlap = self._build_overlap(options)
        self._rules.append(
            Rule(
                predicate=pred,
                overlap=overlap,
                handler_id=handler_id,
                ack=str(options.get("ack", "")),
            )
        )

    def _apply_filters(self, pred: Predicate, options: dict[str, Any]) -> Predicate:
        if "channel" in options:
            ch = options["channel"]
            channels = (ch,) if isinstance(ch, str) else tuple(ch)
            pred = And(left=pred, right=MatchChannel(channels=channels))

        if "kind" in options:
            pred = And(left=pred, right=MatchChatKind(chat_kind=options["kind"]))

        return pred

    def _build_overlap(self, options: dict[str, Any]) -> Overlap:
        policy_str = options.get("overlap", "queue")
        policy = OverlapPolicy(policy_str)
        bound = options.get("bound", 16)
        return Overlap(policy=policy, bound=bound)


__all__ = ["Caspian", "HandlerContext"]
