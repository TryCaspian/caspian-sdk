"""Core domain types — frozen Pydantic models, branded ids, discriminated unions.

Everything here must be decidable without a network. No I/O imports.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

# ─── Branded IDs ─────────────────────────────────────────────────────────────

ThreadId = NewType("ThreadId", str)
ConnectionId = NewType("ConnectionId", str)

# ─── Chat kinds ──────────────────────────────────────────────────────────────

ChatKind = Literal["dm", "group", "channel"]

# ─── Events (inbound from adapter.parse) ─────────────────────────────────────


class Message(BaseModel):
    """A text/media message from a user."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["message"] = "message"
    thread_id: ThreadId
    text: str
    chat_kind: ChatKind
    sender: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    """A button press / callback / interaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["action"] = "action"
    thread_id: ThreadId
    data: str
    sender: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class Reaction(BaseModel):
    """An emoji reaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["reaction"] = "reaction"
    thread_id: ThreadId
    emoji: str
    sender: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


Event = Annotated[Message | Action | Reaction, Field(discriminator="kind")]

# ─── Overlap policy ──────────────────────────────────────────────────────────


class OverlapPolicy(StrEnum):
    QUEUE = "queue"
    DEBOUNCE = "debounce"
    DROP = "drop"
    PARALLEL = "parallel"


class Overlap(BaseModel):
    """Overlap configuration for a rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    policy: OverlapPolicy = OverlapPolicy.QUEUE
    bound: int = 16


# ─── Commands (output from step) ─────────────────────────────────────────────
# Defined in commands.py, re-exported via Command union.

# ─── Rule & App ──────────────────────────────────────────────────────────────

from caspian.core.predicates import Predicate  # noqa: E402


class Rule(BaseModel):
    """A single rule: when (predicate) → how to overlap → what to do (commands)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    predicate: Predicate  # type: ignore[type-arg]
    overlap: Overlap = Overlap()
    handler_id: str = ""


class App(BaseModel):
    """The program: a list of rules. Inspectable, serializable, testable."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    rules: tuple[Rule, ...] = ()
