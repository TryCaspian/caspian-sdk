"""Tests for the golden vectors — proving the kernel works without I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from caspian.core.step import StepState, step
from caspian.core.types import Action, App, Event, Message, Reaction

VECTORS_PATH = Path(__file__).resolve().parents[3] / "vectors" / "step_vectors.json"


def load_vectors() -> list[dict]:
    with open(VECTORS_PATH) as f:
        return json.load(f)


def parse_event(data: dict) -> Event:
    kind = data["kind"]
    if kind == "message":
        return Message(**data)
    elif kind == "action":
        return Action(**data)
    elif kind == "reaction":
        return Reaction(**data)
    raise ValueError(f"Unknown event kind: {kind}")


def parse_app(data: dict) -> App:
    return App.model_validate(data)


class TestGoldenVectors:
    """Replay shared golden vectors. Both TS and Python must produce identical results."""

    @pytest.fixture
    def vectors(self) -> list[dict]:
        return load_vectors()

    def test_single_event_vectors(self, vectors: list[dict]) -> None:
        for vec in vectors:
            if "events" in vec:
                continue

            state = StepState()
            app = parse_app(vec["app"])
            event = parse_event(vec["event"])
            channel_name = vec.get("channel_name", "")

            result = step(state, event, app, channel_name=channel_name)

            expected_cmds = vec["expected_commands"]
            assert len(result.commands) == len(expected_cmds), (
                f"Vector '{vec['name']}': expected {len(expected_cmds)} commands, "
                f"got {len(result.commands)}"
            )

            for cmd, exp in zip(result.commands, expected_cmds, strict=False):
                actual_tag = getattr(cmd, "tag", None)
                assert actual_tag == exp["tag"], (
                    f"Vector '{vec['name']}': expected tag {exp['tag']}, "
                    f"got {actual_tag}"
                )

            assert result.dropped == vec["expected_dropped"], (
                f"Vector '{vec['name']}': expected dropped={vec['expected_dropped']}, "
                f"got {result.dropped}"
            )

    def test_multi_event_vectors(self, vectors: list[dict]) -> None:
        for vec in vectors:
            if "events" not in vec:
                continue

            state = StepState()
            app = parse_app(vec["app"])
            channel_name = vec.get("channel_name", "")
            expected_results = vec["expected_results"]

            for i, event_data in enumerate(vec["events"]):
                event = parse_event(event_data)
                result = step(state, event, app, channel_name=channel_name)

                exp = expected_results[i]
                assert len(result.commands) == exp["commands_count"], (
                    f"Vector '{vec['name']}' event {i}: expected {exp['commands_count']} "
                    f"commands, got {len(result.commands)}"
                )
                assert result.dropped == exp["dropped"], (
                    f"Vector '{vec['name']}' event {i}: expected dropped={exp['dropped']}, "
                    f"got {result.dropped}"
                )
