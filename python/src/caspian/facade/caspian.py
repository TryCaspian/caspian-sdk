"""Caspian — the B-surface facade. What bot developers import and write against.

cx.on_message({...}, handler) builds Rules. The App is inspectable data.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol

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
    Overlap,
    OverlapPolicy,
    Rule,
)
from caspian.facade.channels import ChannelManager
from caspian.interpreters import ProcessInterpreter

Handler = Callable[..., Any]


class Transport(Protocol):
    def dispatch(self, sent: Sent) -> Result: ...


class HandlerContext:
    """Context passed to handlers alongside the thread and event."""

    def __init__(self, *, skipped: int = 0) -> None:
        self.skipped = skipped


class Caspian:
    """The public SDK entry point. Builds an App of Rules from on_message/on_action calls.

    The App is pure data — inspectable, serializable, testable without a network.

    To process real inbound webhooks, add channels and call handle():

        cx = Caspian()
        cx.channels.add("telegram", via="self-host", bot_token=TG, webhook_url=URL)

        @cx.on_message({"channel": "telegram"})
        def reply(thread, msg, ctx):
            thread.post(f"you said: {msg.text}")

        # in your own FastAPI/Flask route:
        results = cx.handle("telegram", request_body, request_headers)

    The developer owns the HTTP server; the SDK owns the pipeline
    (verify → parse → step → handlers → execute → transport).
    """

    def __init__(self, *, transport: Transport | None = None, dispatch: bool = True) -> None:
        self._rules: list[Rule] = []
        self._handlers: dict[str, Handler] = {}
        self.channels = ChannelManager()
        self._interpreters: dict[str, ProcessInterpreter] = {}
        self._dispatch = dispatch
        self._transport = transport
        if dispatch and transport is None:
            # Default to real HTTP dispatch. Import here so pure/test use
            # (memory interpreter, dispatch=False) never requires httpx wiring.
            from caspian.interpreters.transport import HttpTransport

            self._transport = HttpTransport()

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
        """Composition root: drive one raw inbound webhook through the full pipeline.

        verify → parse → step → run handlers → execute → transport.

        The developer calls this from their own HTTP route. Returns one Result per
        executed command (or a single error Result on verify/parse failure).
        Per-channel overlap state persists across calls.
        """
        interp = self._interpreter_for(channel)
        return interp.handle_webhook(RawInbound(body=body, headers=headers or {}))

    def _interpreter_for(self, channel: str) -> ProcessInterpreter:
        """Get-or-create the ProcessInterpreter for a channel (preserves overlap state)."""
        if channel not in self._interpreters:
            adapter = self.channels.adapter_for(channel)
            connection = self.channels.connection_for(channel)
            self._interpreters[channel] = ProcessInterpreter(
                self.app,
                adapter,
                connection,
                handlers=self._handlers,
                transport=self._transport if self._dispatch else None,
            )
        return self._interpreters[channel]

    def on_message(
        self,
        options: dict[str, Any] | None = None,
        handler: Handler | None = None,
    ) -> Callable[[Handler], Handler] | None:
        """Register a message handler. Can be used as a decorator or called directly.

        Options:
            channel: str | list[str] — filter by channel name(s)
            kind: "dm" | "group" | "channel" — filter by chat kind
            overlap: "queue" | "debounce" | "drop" | "parallel"
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
        self._rules.append(Rule(predicate=pred, overlap=overlap, handler_id=handler_id))

    def _register_action_handler(self, options: dict[str, Any], fn: Handler) -> None:
        handler_id = f"handler_{uuid.uuid4().hex[:8]}"
        self._handlers[handler_id] = fn

        pred: Predicate = MatchKind(kind="action")
        pred = self._apply_filters(pred, options)

        overlap = self._build_overlap(options)
        self._rules.append(Rule(predicate=pred, overlap=overlap, handler_id=handler_id))

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
