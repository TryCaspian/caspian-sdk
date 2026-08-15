"""Process interpreter — runs an App against real webhook bytes.

webhook bytes → adapter.verify → adapter.parse → step → adapter.execute → transport
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from caspian.core.commands import Command, Host
from caspian.core.ports import AdapterPort, Connection, RawInbound, Result, Sent
from caspian.core.step import StepResult, StepState, step
from caspian.core.types import App, Event
from caspian.facade.thread import Thread
from caspian.interpreters.transport import HttpTransport

Handler = Callable[..., Any]


class Transport(Protocol):
    def dispatch(self, sent: Sent) -> Result: ...


class HandlerContext:
    """Context passed to handlers in process mode."""

    def __init__(self, *, skipped: int = 0) -> None:
        self.skipped = skipped


class ProcessInterpreter:
    """Interpreter for self-hosted / BYO-token mode.

    Receives raw webhook bytes, verifies, parses via adapter, runs step,
    executes resulting commands via adapter, then dispatches over the transport.
    If no transport is given, execute() results (request descriptions) are
    returned undispatched — useful for tests.
    """

    def __init__(
        self,
        app: App,
        adapter: AdapterPort,
        connection: Connection,
        handlers: dict[str, Handler] | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._app = app
        self._adapter = adapter
        self._connection = connection
        self._handlers = handlers or {}
        self._state = StepState()
        self._transport = transport

    def handle_webhook(self, raw: RawInbound) -> list[Result]:
        """Process a raw webhook payload end-to-end. Returns per-command results."""
        verify = getattr(self._adapter, "verify", None)
        if callable(verify) and not verify(raw, self._connection):
            from caspian.core.errors import DecodeError

            return [Result.err(DecodeError(reason="Webhook signature verification failed"))]

        parse_result = self._adapter.parse(raw)
        if not parse_result.is_ok:
            return [parse_result]

        events: list[Event] = parse_result.value
        all_results: list[Result] = []

        for event in events:
            all_results.extend(self._handle_event(event))

        return all_results

    def _handle_event(self, event: Event) -> list[Result]:
        results: list[Result] = []

        # Ack law: let the adapter acknowledge interactions immediately.
        acknowledge = getattr(self._adapter, "acknowledge", None)
        if callable(acknowledge):
            ack = acknowledge(event, self._connection)
            if ack is not None:
                results.append(self._maybe_dispatch(ack))

        overlap_key = self._adapter.overlap_key(event)
        step_result = step(
            self._state,
            event,
            self._app,
            channel_name=self._adapter.name,
            overlap_key=overlap_key,
        )

        if step_result.dropped or not step_result.commands:
            return results

        for cmd in self._resolve_host_commands(step_result, event):
            exec_result = self._adapter.execute(cmd, self._connection)
            results.append(self._maybe_dispatch(exec_result))

        return results

    def _maybe_dispatch(self, exec_result: Result) -> Result:
        """If a transport is configured and the command produced a request, dispatch it."""
        if not exec_result.is_ok or self._transport is None:
            return exec_result
        sent = exec_result.value
        if isinstance(sent, Sent) and sent.raw.get("transport"):
            return self._transport.dispatch(sent)
        return exec_result

    def _resolve_host_commands(
        self, step_result: StepResult, event: Event
    ) -> list[Command]:
        """Run Host commands through registered handlers, collect their output commands."""
        final_commands: list[Command] = []

        for cmd in step_result.commands:
            if isinstance(cmd, Host):
                handler = self._handlers.get(cmd.handler_id)
                if handler is None:
                    continue
                thread = Thread(thread_id=event.thread_id)
                handler(thread, event, HandlerContext(skipped=step_result.skipped_count))
                final_commands.extend(thread.commands)
            else:
                final_commands.append(cmd)

        return final_commands


__all__ = ["HandlerContext", "HttpTransport", "ProcessInterpreter", "Transport"]
