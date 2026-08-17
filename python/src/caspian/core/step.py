"""step() — the kernel's pure function.

step(state, event, app, channel_name) → (new_state, list[Command])

No I/O, no clock, no randomness. This is what makes the bot testable.
"""

from __future__ import annotations

from caspian.core.commands import Command, Host, Post, Typing
from caspian.core.overlap import (
    OverlapDecision,
    OverlapState,
    overlap_transition,
)
from caspian.core.predicates import evaluate
from caspian.core.types import App, Event, Rule, ThreadId


class StepState:
    """Mutable state bag carried across steps. Owned by the runner, read by step."""

    def __init__(self) -> None:
        self.overlap_states: dict[str, OverlapState] = {}

    def get_overlap(self, key: str) -> OverlapState:
        return self.overlap_states.get(key, OverlapState())

    def set_overlap(self, key: str, state: OverlapState) -> None:
        self.overlap_states[key] = state


class StepResult:
    """Output of one step: commands to execute + updated state."""

    def __init__(
        self,
        commands: list[Command],
        *,
        matched_rule: Rule | None = None,
        skipped_count: int = 0,
        dropped: bool = False,
    ) -> None:
        self.commands = commands
        self.matched_rule = matched_rule
        self.skipped_count = skipped_count
        self.dropped = dropped


def _get_thread_id(event: Event) -> ThreadId:
    """Extract thread_id from any event variant."""
    return event.thread_id


def step(
    state: StepState,
    event: Event,
    app: App,
    *,
    channel_name: str = "",
    overlap_key: str | None = None,
) -> StepResult:
    """Pure step: match event against rules, apply overlap, return commands.

    The overlap_key defaults to the event's thread_id if not provided
    (the adapter normally supplies it via overlap_key()).
    """
    thread_id = _get_thread_id(event)
    key = overlap_key or str(thread_id)
    # Buttons never share the text queue (core-discipline overlap law).
    if getattr(event, "kind", None) == "action":
        key = f"{key}::action"

    for rule in app.rules:
        if not evaluate(rule.predicate, event, channel_name=channel_name):
            continue

        current_overlap = state.get_overlap(key)
        result = overlap_transition(
            current_overlap,
            rule.overlap.policy,
            rule.overlap.bound,
        )
        state.set_overlap(key, result.new_state)

        match result.decision:
            case OverlapDecision.DROP:
                return StepResult(commands=[], matched_rule=rule, dropped=True)

            case OverlapDecision.ENQUEUE:
                return StepResult(commands=[], matched_rule=rule, dropped=False)

            case OverlapDecision.EXECUTE:
                commands: list[Command] = []
                if rule.ack:
                    commands.append(Post(thread_id=thread_id, text=rule.ack))
                commands.append(Typing(thread_id=thread_id))
                commands.append(Host(handler_id=rule.handler_id))
                return StepResult(
                    commands=commands,
                    matched_rule=rule,
                    skipped_count=result.skipped_count,
                )

    return StepResult(commands=[], matched_rule=None)
