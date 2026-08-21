"""Caspian — the B-surface facade. What bot developers import and write against.

cx.on_message({...}, handler) builds Rules. The App is inspectable data.
Self-host, poll, and hosted all run the same App through ProcessInterpreter.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, Literal, TypeVar, overload

from caspian.catalog import CHANNELS, ChannelName, SocketKind, socket_channels
from caspian.core.errors import AuthRequired, ProvisionError
from caspian.core.interpreter_memory import MemoryInterpreter
from caspian.core.ports import RawInbound, Result, TransportPort
from caspian.core.predicates import (
    And,
    MatchChannel,
    MatchChatKind,
    MatchCommand,
    MatchData,
    MatchKind,
    Predicate,
    command_of,
)
from caspian.core.types import (
    App,
    ConnectionId,
    Overlap,
    OverlapPolicy,
    Rule,
)
from caspian.facade.channels import ChannelManager
from caspian.facade.host import ActionHandler, FacadeHost, HandlerContext, MessageHandler
from caspian.facade.options import OnActionOptions, OnMessageOptions
from caspian.facade.thread import Thread
from caspian.interpreters.process import ProcessInterpreter
from caspian.tools import ToolSet

Handler = Callable[..., Any]
_MsgH = TypeVar("_MsgH", bound=MessageHandler)
_ActH = TypeVar("_ActH", bound=ActionHandler)


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
        transport: TransportPort | None = None,
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
        self._interpreters: dict[str, ProcessInterpreter] = {}
        self._transport = transport
        if dispatch and transport is None:
            self._transport = self._default_transport()
        self.channels: ChannelManager = ChannelManager(
            gateway_client=self._gateway_client,
            transport=self._transport if dispatch else None,
        )

    def _default_transport(self) -> TransportPort:
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
        channel: ChannelName | Literal["gateway"],
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> list[Result]:
        """Drive one inbound webhook through verify → parse → step → handlers → send.

        Use this for self-host HTTP. ``channel`` must have been added with
        ``via="self-host"``. Hosted inbound uses ``handle("gateway", ...)`` or
        ``run()`` — not ``handle("telegram", ...)``.

        Signatures are checked. Poll and socket inbound skip that check because
        the bot token already authenticated the session.
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
        channel: ChannelName,
        *,
        transport: TransportPort | None = None,
        max_iterations: int | None = None,
        offset: int = 0,
        interval: float = 1.0,
    ) -> list[Result]:
        """Self-host long-poll. Each update is fed to the same pipeline as handle().

        Use when the process has no public URL (Telegram getUpdates). The bot
        token authenticates the fetch, so webhook signatures are not checked.
        Waits ``interval`` seconds between polls so the client does not spin.
        """
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
            lambda raw: interp.handle_webhook(raw, trusted=True),
            transport=transport or self._transport,  # type: ignore[arg-type]
            offset=offset,
            sleep=time.sleep,
        )
        return runner.run_forever(max_iterations=max_iterations, sleep=interval)

    def listen(self, channel: str = "discord", *, max_events: int | None = None) -> list[Result]:
        """Self-host inbound over a held-open socket. No public URL needed.

        Channels whose catalog row has a socket inbound. Today that is discord
        (the only inbound path) and slack (Socket Mode, alternative to webhook).

        Blocks for the life of the process; each inbound goes through the same
        handle_webhook as every other channel, so handler, ack, streaming and
        sending are identical to the webhook path.

            cx.channels.add("discord", via="self-host", bot_token=TOKEN)
            cx.listen("discord")

            cx.channels.add("slack", via="self-host",
                            bot_token="xoxb-...", app_token="xapp-...")
            cx.listen("slack")

        Requires the optional websockets dependency: caspian[discord] or
        caspian[slack-socket].
        """
        allowed = socket_channels()
        if channel not in allowed:
            names = ", ".join(allowed)
            return [
                Result.err(
                    ProvisionError(
                        reason=f"listen() supports {names}, not {channel!r}; "
                        f"use run() for hosted or handle() for webhook self-host"
                    )
                )
            ]
        owner = self.channels.inbound_owner(channel)
        if owner != "local":
            return [
                Result.err(
                    ProvisionError(
                        reason=f"Inbound for {channel!r} is owned by the gateway; use run()"
                    )
                )
            ]
        import asyncio

        connection = self.channels.connection_for(channel)
        interp = self._interpreter_for(channel)
        row = CHANNELS[channel]

        if row.socket is SocketKind.SLACK:
            from caspian.interpreters.slack_socket import SlackSocketRunner

            app_token = connection.config.get("app_token", "")
            if not app_token:
                return [
                    Result.err(
                        ProvisionError(
                            reason="slack socket mode needs an app_token (xapp-, scope "
                            "connections:write) alongside the bot_token; without a "
                            "public URL there is no webhook to fall back to"
                        )
                    )
                ]
            return asyncio.run(
                SlackSocketRunner(
                    app_token, lambda raw: interp.handle_webhook(raw, trusted=True)
                ).run(max_events=max_events)
            )

        from caspian.interpreters.discord_gateway import DiscordGatewayRunner

        token = connection.config.get("bot_token", "")
        if not token:
            return [Result.err(ProvisionError(reason="discord self-host needs a bot_token"))]
        return asyncio.run(
            DiscordGatewayRunner(
                token, lambda raw: interp.handle_webhook(raw, trusted=True)
            ).run(max_events=max_events)
        )

    def run(
        self, *, max_iterations: int | None = None, interval: float = 1.0
    ) -> list[Result]:
        """Hosted poll loop: GET /v1/events then handle('gateway', ...).

        Requires ``Caspian(api_key=...)``. Waits ``interval`` seconds between
        polls so the client does not spin.
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

    @overload
    def on_message(
        self, options: OnMessageOptions | None = None
    ) -> Callable[[_MsgH], _MsgH]: ...

    @overload
    def on_message(
        self, options: OnMessageOptions | None, handler: _MsgH
    ) -> None: ...

    def on_message(
        self,
        options: OnMessageOptions | None = None,
        handler: _MsgH | None = None,
    ) -> Callable[[_MsgH], _MsgH] | None:
        """Register a message handler. Decorator or ``on_message(opts, fn)``.

        Options: ``channel``, ``kind`` (dm/group/channel), ``command``
        (``/help``), ``overlap`` (queue/debounce/drop/parallel/stream),
        ``bound``, ``ack`` (instant reply before the handler runs).
        """
        if handler is not None:
            self._register_message_handler(dict(options or {}), handler)
            return None

        def decorator(fn: _MsgH) -> _MsgH:
            self._register_message_handler(dict(options or {}), fn)
            return fn

        return decorator

    @overload
    def on_action(
        self, options: OnActionOptions | None = None
    ) -> Callable[[_ActH], _ActH]: ...

    @overload
    def on_action(
        self, options: OnActionOptions | None, handler: _ActH
    ) -> None: ...

    def on_action(
        self,
        options: OnActionOptions | None = None,
        handler: _ActH | None = None,
    ) -> Callable[[_ActH], _ActH] | None:
        """Register a button / callback handler. Same overlap options as on_message."""
        if handler is not None:
            self._register_action_handler(dict(options or {}), handler)
            return None

        def decorator(fn: _ActH) -> _ActH:
            self._register_action_handler(dict(options or {}), fn)
            return fn

        return decorator

    def use(self, rule: Rule) -> None:
        """Power-user escape: add a raw Rule directly (A-level API)."""
        self._rules.append(rule)

    def tools(
        self,
        thread: Thread | None = None,
        *,
        preset: Literal["messenger", "outbound"] = "messenger",
    ) -> ToolSet:
        """Agent-callable tools derived from Command types.

        Models address thread_ids, never raw platform chat ids. ``preset`` is
        messenger (bound to the current thread) or outbound (must name a thread).
        """
        return ToolSet(thread, preset=preset)

    def interpret(self) -> MemoryInterpreter:
        """Create a MemoryInterpreter for testing this app, with no network."""
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

        if "command" in options:
            raw = options["command"]
            names = (raw,) if isinstance(raw, str) else tuple(raw)
            pred = And(
                left=pred,
                right=MatchCommand(names=tuple(command_of(n) for n in names)),
            )

        if "data" in options:
            raw = options["data"]
            values = (raw,) if isinstance(raw, str) else tuple(raw)
            pred = And(left=pred, right=MatchData(values=tuple(values)))

        return pred

    def _build_overlap(self, options: dict[str, Any]) -> Overlap:
        policy_str = options.get("overlap", "queue")
        policy = OverlapPolicy(policy_str)
        bound = options.get("bound", 16)
        return Overlap(policy=policy, bound=bound)


__all__ = ["Caspian", "HandlerContext"]
