"""Process interpreter — runs an App against real webhook bytes.

webhook bytes → adapter.parse → step → adapter.execute
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from caspian.core.commands import Command, Host
from caspian.core.ports import AdapterPort, Connection, RawInbound, Result
from caspian.core.step import StepResult, StepState, step
from caspian.core.types import App, Event
from caspian.facade.thread import Thread

Handler = Callable[..., Any]


class ProcessInterpreter:
    """Interpreter for self-hosted / BYO-token mode.

    Receives raw webhook bytes, parses via adapter, runs step,
    executes resulting commands via adapter.
    """

    def __init__(
        self,
        app: App,
        adapter: AdapterPort,
        connection: Connection,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self._app = app
        self._adapter = adapter
        self._connection = connection
        self._handlers = handlers or {}
        self._state = StepState()

    def handle_webhook(self, raw: RawInbound) -> list[Result]:
        """Process a raw webhook payload end-to-end.

        Returns results for each command executed.
        """
        parse_result = self._adapter.parse(raw)
        if not parse_result.is_ok:
            return [parse_result]

        events: list[Event] = parse_result.value
        all_results: list[Result] = []

        for event in events:
            overlap_key = self._adapter.overlap_key(event)
            channel_name = self._adapter.name

            step_result = step(
                self._state,
                event,
                self._app,
                channel_name=channel_name,
                overlap_key=overlap_key,
            )

            if step_result.dropped or not step_result.commands:
                continue

            commands_to_execute = self._resolve_host_commands(
                step_result, event
            )

            for cmd in commands_to_execute:
                exec_result = self._adapter.execute(cmd, self._connection)
                all_results.append(exec_result)

        return all_results

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

                thread = Thread(thread_id=event.thread_id)  # type: ignore[union-attr]
                handler(thread, event, HandlerContext(skipped=step_result.skipped_count))
                final_commands.extend(thread.commands)
            else:
                final_commands.append(cmd)

        return final_commands


class HandlerContext:
    """Context passed to handlers in process mode."""

    def __init__(self, *, skipped: int = 0) -> None:
        self.skipped = skipped
