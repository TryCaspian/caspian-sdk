"""FacadeHost — the B-surface implementation of core HostPort.

The interpreter never imports this module. It only sees HostPort and receives
Commands back. Thread lives here so interpreters/ cannot depend on facade.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from caspian.core.commands import Command
from caspian.core.types import Event
from caspian.facade.thread import Thread

Handler = Callable[..., Any]


class HandlerContext:
    """Context passed to handlers alongside the thread and event."""

    def __init__(self, *, skipped: int = 0) -> None:
        self.skipped = skipped


class FacadeHost:
    """HostPort: run a registered handler and collect the Commands it enqueued."""

    def __init__(self, handlers: dict[str, Handler]) -> None:
        self._handlers = handlers

    def run(
        self, handler_id: str, event: Event, *, skipped_count: int = 0
    ) -> list[Command]:
        handler = self._handlers.get(handler_id)
        if handler is None:
            return []
        thread = Thread(thread_id=event.thread_id)
        result = handler(thread, event, HandlerContext(skipped=skipped_count))
        if inspect.iscoroutine(result):
            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
        return thread.commands
