"""FacadeHost — the B-surface implementation of core HostPort.

The interpreter never imports this module. It only sees HostPort and receives
Commands back. Thread lives here so interpreters/ cannot depend on facade.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from caspian.core.commands import Command
from caspian.core.types import Action, Event, Message
from caspian.facade.thread import Thread

Handler = Callable[..., Any]


class HandlerContext:
    """Per-turn context passed to handlers as the third argument.

    ``skipped`` is how many inbound events overlap dropped or queued *before*
    this turn ran. It is not a count of events your handler ignored.
    """

    def __init__(self, *, skipped: int = 0) -> None:
        self.skipped = skipped


class MessageHandler(Protocol):
    """``(thread, message, ctx)`` — what ``on_message`` registers.

    Parameters are positional-only so the editor accepts ``msg`` / ``message``.
    """

    def __call__(
        self, thread: Thread, message: Message, ctx: HandlerContext, /
    ) -> object | Awaitable[object]: ...


class ActionHandler(Protocol):
    """``(thread, action, ctx)`` — what ``on_action`` registers.

    Parameters are positional-only so the editor accepts ``act`` / ``action``.
    """

    def __call__(
        self, thread: Thread, action: Action, ctx: HandlerContext, /
    ) -> object | Awaitable[object]: ...


class FacadeHost:
    """HostPort: run a registered handler and collect the Commands it enqueued."""

    def __init__(self, handlers: dict[str, Handler]) -> None:
        self._handlers = handlers

    def run(
        self,
        handler_id: str,
        event: Event,
        *,
        skipped_count: int = 0,
        sink: Any = None,
    ) -> list[Command]:
        handler = self._handlers.get(handler_id)
        if handler is None:
            return []
        thread = Thread(thread_id=event.thread_id, sink=sink)
        result = handler(thread, event, HandlerContext(skipped=skipped_count))
        if inspect.iscoroutine(result):
            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, result).result()
        return thread.commands
