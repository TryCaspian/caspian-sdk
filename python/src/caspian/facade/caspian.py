"""Caspian — the B-surface facade. What bot developers import and write against.

cx.on_message({...}, handler) builds Rules. The App is inspectable data.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from caspian.core.interpreter_memory import MemoryInterpreter
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

Handler = Callable[..., Any]


class HandlerContext:
    """Context passed to handlers alongside the thread and event."""

    def __init__(self, *, skipped: int = 0) -> None:
        self.skipped = skipped


class Caspian:
    """The public SDK entry point. Builds an App of Rules from on_message/on_action calls.

    The App is pure data — inspectable, serializable, testable without a network.
    """

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self._handlers: dict[str, Handler] = {}

    @property
    def app(self) -> App:
        """The current program as inspectable data."""
        return App(rules=tuple(self._rules))

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
