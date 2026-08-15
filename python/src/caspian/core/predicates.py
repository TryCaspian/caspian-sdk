"""Predicates — composable filters for matching Events to Rules.

Python-operator-friendly: `message & channel("telegram") & ~dm()`
This is the power-user A API, not the README surface.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MatchAll(BaseModel):
    """Always matches."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["all"] = "all"


class MatchKind(BaseModel):
    """Match event kind (message / action / reaction)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["kind"] = "kind"
    kind: Literal["message", "action", "reaction"]


class MatchChannel(BaseModel):
    """Match by channel name(s)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["channel"] = "channel"
    channels: tuple[str, ...]


class MatchChatKind(BaseModel):
    """Match by chat kind (dm / group / channel)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["chat_kind"] = "chat_kind"
    chat_kind: Literal["dm", "group", "channel"]


class And(BaseModel):
    """Logical AND of predicates."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["and"] = "and"
    left: Predicate
    right: Predicate


class Or(BaseModel):
    """Logical OR of predicates."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["or"] = "or"
    left: Predicate
    right: Predicate


class Not(BaseModel):
    """Logical NOT of a predicate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["not"] = "not"
    inner: Predicate


Predicate = Annotated[
    MatchAll | MatchKind | MatchChannel | MatchChatKind | And | Or | Not,
    Field(discriminator="op"),
]

# Rebuild models after forward-ref Predicate is fully defined
And.model_rebuild()
Or.model_rebuild()
Not.model_rebuild()


# ─── Convenience constructors ────────────────────────────────────────────────


def message() -> MatchKind:
    return MatchKind(kind="message")


def action() -> MatchKind:
    return MatchKind(kind="action")


def reaction() -> MatchKind:
    return MatchKind(kind="reaction")


def channel(*names: str) -> MatchChannel:
    return MatchChannel(channels=tuple(names))


def dm() -> MatchChatKind:
    return MatchChatKind(chat_kind="dm")


def group() -> MatchChatKind:
    return MatchChatKind(chat_kind="group")


# ─── Evaluate a predicate against an event + metadata ────────────────────────


def evaluate(pred: Predicate, event: Any, *, channel_name: str = "") -> bool:  # noqa: ANN401
    """Pure evaluation: does this event match the predicate?"""
    match pred:
        case MatchAll():
            return True
        case MatchKind(kind=k):
            return getattr(event, "kind", None) == k
        case MatchChannel(channels=chs):
            return channel_name in chs
        case MatchChatKind(chat_kind=ck):
            return getattr(event, "chat_kind", None) == ck
        case And(left=l, right=r):
            return evaluate(l, event, channel_name=channel_name) and evaluate(
                r, event, channel_name=channel_name
            )
        case Or(left=l, right=r):
            return evaluate(l, event, channel_name=channel_name) or evaluate(
                r, event, channel_name=channel_name
            )
        case Not(inner=inner):
            return not evaluate(inner, event, channel_name=channel_name)
        case _:
            return False
