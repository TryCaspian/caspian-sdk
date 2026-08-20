"""Tests for overlap FSM — pure state transitions."""

from __future__ import annotations

from caspian.core.overlap import (
    OverlapDecision,
    OverlapState,
    SlotStatus,
    drain_transition,
    overlap_transition,
)
from caspian.core.types import OverlapPolicy


class TestOverlapTransitions:
    def test_queue_idle_executes(self) -> None:
        state = OverlapState()
        result = overlap_transition(state, OverlapPolicy.QUEUE, bound=16)
        assert result.decision == OverlapDecision.EXECUTE
        assert result.new_state.status == SlotStatus.BUSY

    def test_queue_busy_enqueues(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY)
        result = overlap_transition(state, OverlapPolicy.QUEUE, bound=16)
        assert result.decision == OverlapDecision.ENQUEUE
        assert result.new_state.queued == 1

    def test_queue_at_bound_drops(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY, queued=3)
        result = overlap_transition(state, OverlapPolicy.QUEUE, bound=3)
        assert result.decision == OverlapDecision.DROP

    def test_drop_idle_executes(self) -> None:
        state = OverlapState()
        result = overlap_transition(state, OverlapPolicy.DROP, bound=16)
        assert result.decision == OverlapDecision.EXECUTE

    def test_drop_busy_drops(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY)
        result = overlap_transition(state, OverlapPolicy.DROP, bound=16)
        assert result.decision == OverlapDecision.DROP

    def test_parallel_always_executes(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY, queued=5)
        result = overlap_transition(state, OverlapPolicy.PARALLEL, bound=16)
        assert result.decision == OverlapDecision.EXECUTE

    def test_debounce_busy_replaces(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY, queued=1)
        result = overlap_transition(state, OverlapPolicy.DEBOUNCE, bound=16)
        assert result.decision == OverlapDecision.ENQUEUE
        assert result.new_state.queued == 1  # always 1, replaces

    def test_drain_with_queued_executes(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY, queued=2, skipped_count=2)
        result = drain_transition(state, OverlapPolicy.QUEUE)
        assert result.decision == OverlapDecision.EXECUTE
        assert result.skipped_count == 2

    def test_drain_empty_goes_idle(self) -> None:
        state = OverlapState(status=SlotStatus.BUSY, queued=0)
        result = drain_transition(state, OverlapPolicy.QUEUE)
        assert result.new_state.status == SlotStatus.IDLE
