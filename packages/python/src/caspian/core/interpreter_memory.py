"""Memory interpreter — runs an App purely in-memory. No network, no state backend.

This is the first real win: test a bot without Telegram, without a gateway,
without any server. Feed events in, get commands out.
"""

from __future__ import annotations

from caspian.core.commands import Command
from caspian.core.step import StepResult, StepState, step
from caspian.core.types import App, Event


class MemoryInterpreter:
    """In-memory interpreter: records commands instead of executing them.

    Use for testing, golden-vector generation, and dry-runs.
    """

    def __init__(self) -> None:
        self.state = StepState()
        self.executed_commands: list[Command] = []
        self.handler_results: dict[str, list[Command]] = {}
        self._handlers: dict[str, object] = {}

    def register_handler(self, handler_id: str, fn: object) -> None:
        """Register a handler function (used by the facade)."""
        self._handlers[handler_id] = fn

    def run(
        self,
        app: App,
        event: Event,
        *,
        channel_name: str = "",
        overlap_key: str | None = None,
    ) -> StepResult:
        """Run one step: event in, commands out. No I/O."""
        result = step(
            self.state,
            event,
            app,
            channel_name=channel_name,
            overlap_key=overlap_key,
        )
        self.executed_commands.extend(result.commands)
        return result

    def run_sequence(
        self,
        app: App,
        events: list[tuple[Event, str]],
    ) -> list[StepResult]:
        """Run a sequence of (event, channel_name) pairs. Returns all results."""
        results: list[StepResult] = []
        for event, ch in events:
            results.append(self.run(app, event, channel_name=ch))
        return results

    def commands_for(self, tag: str) -> list[Command]:
        """Filter executed commands by tag."""
        return [c for c in self.executed_commands if getattr(c, "tag", None) == tag]

    def reset(self) -> None:
        """Clear all state and recorded commands."""
        self.state = StepState()
        self.executed_commands.clear()
        self.handler_results.clear()
