"""Overlap FSM — pure state transitions, no asyncio.Queue, no I/O.

The overlap policy is named on the Rule. The state machine computes transitions.
The runner (memory / process / hosted) executes the actual queuing.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from caspian.core.types import OverlapPolicy


class SlotStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"


class OverlapState(BaseModel):
    """Per-key overlap state. Managed by the runner, transitions computed by core."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: SlotStatus = SlotStatus.IDLE
    queued: int = 0
    skipped_count: int = 0


class OverlapDecision(StrEnum):
    EXECUTE = "execute"
    ENQUEUE = "enqueue"
    DROP = "drop"


class OverlapResult(BaseModel):
    """Result of an overlap transition."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    decision: OverlapDecision
    new_state: OverlapState
    skipped_count: int = 0


def overlap_transition(
    state: OverlapState,
    policy: OverlapPolicy,
    bound: int,
) -> OverlapResult:
    """Pure transition: given current state + policy, decide what to do with a new event.

    Returns the decision (execute/enqueue/drop) and the new state.
    """
    match policy:
        case OverlapPolicy.PARALLEL:
            return OverlapResult(
                decision=OverlapDecision.EXECUTE,
                new_state=state,
            )

        case OverlapPolicy.DROP:
            if state.status == SlotStatus.BUSY:
                return OverlapResult(
                    decision=OverlapDecision.DROP,
                    new_state=state,
                )
            return OverlapResult(
                decision=OverlapDecision.EXECUTE,
                new_state=OverlapState(status=SlotStatus.BUSY),
            )

        case OverlapPolicy.QUEUE:
            if state.status == SlotStatus.IDLE:
                return OverlapResult(
                    decision=OverlapDecision.EXECUTE,
                    new_state=OverlapState(status=SlotStatus.BUSY, queued=0),
                )
            if state.queued >= bound:
                return OverlapResult(
                    decision=OverlapDecision.DROP,
                    new_state=state,
                )
            return OverlapResult(
                decision=OverlapDecision.ENQUEUE,
                new_state=OverlapState(
                    status=SlotStatus.BUSY,
                    queued=state.queued + 1,
                    skipped_count=state.skipped_count + 1,
                ),
            )

        case OverlapPolicy.DEBOUNCE:
            if state.status == SlotStatus.IDLE:
                return OverlapResult(
                    decision=OverlapDecision.EXECUTE,
                    new_state=OverlapState(status=SlotStatus.BUSY),
                )
            return OverlapResult(
                decision=OverlapDecision.ENQUEUE,
                new_state=OverlapState(
                    status=SlotStatus.BUSY,
                    queued=1,
                    skipped_count=state.skipped_count + 1,
                ),
            )

        case _:
            return OverlapResult(
                decision=OverlapDecision.EXECUTE,
                new_state=state,
            )


def drain_transition(state: OverlapState, policy: OverlapPolicy) -> OverlapResult:
    """Called when a handler completes. Decides whether to run the next queued event."""
    if state.queued > 0 and policy in (OverlapPolicy.QUEUE, OverlapPolicy.DEBOUNCE):
        return OverlapResult(
            decision=OverlapDecision.EXECUTE,
            new_state=OverlapState(
                status=SlotStatus.BUSY,
                queued=0,
                skipped_count=0,
            ),
            skipped_count=state.skipped_count,
        )
    return OverlapResult(
        decision=OverlapDecision.DROP,
        new_state=OverlapState(status=SlotStatus.IDLE),
    )
