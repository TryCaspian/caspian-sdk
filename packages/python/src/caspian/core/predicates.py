"""Predicates — composable filters for matching Events to Rules.

Python-operator-friendly: `message & channel("telegram") & ~dm()`
This is the power-user A API, not the README surface.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from caspian.catalog import ChannelName


class MatchAll(BaseModel):
    """Always matches."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["all"] = "all"


class MatchKind(BaseModel):
    """Match event kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["kind"] = "kind"
    kind: Literal[
        "message",
        "action",
        "reaction",
        "receipt",
        "member_join",
        "member_leave",
        "edited",
        "deleted",
    ]


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


class MatchCommand(BaseModel):
    """Match the first token of message text (``/help``, ``/help@Bot``, ``/help please``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["command"] = "command"
    names: tuple[str, ...]


class MatchData(BaseModel):
    """Match ``Action.data`` (button callback)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["data"] = "data"
    values: tuple[str, ...]


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
    MatchAll
    | MatchKind
    | MatchChannel
    | MatchChatKind
    | MatchCommand
    | MatchData
    | And
    | Or
    | Not,
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


def receipt() -> MatchKind:
    return MatchKind(kind="receipt")


def member_join() -> MatchKind:
    return MatchKind(kind="member_join")


def member_leave() -> MatchKind:
    return MatchKind(kind="member_leave")


def edited() -> MatchKind:
    return MatchKind(kind="edited")


def deleted() -> MatchKind:
    return MatchKind(kind="deleted")


def channel(*names: ChannelName | str) -> MatchChannel:
    return MatchChannel(channels=tuple(names))


def dm() -> MatchChatKind:
    return MatchChatKind(chat_kind="dm")


def group() -> MatchChatKind:
    return MatchChatKind(chat_kind="group")


def command_of(text: str) -> str:
    """First token, optional ``/``, drop ``@botname``. ``/help@Foo please`` → ``help``."""
    token = text.strip().split(None, 1)[0] if text.strip() else ""
    if token.startswith("/"):
        token = token[1:]
    return token.split("@", 1)[0].lower()


def command(*names: str) -> MatchCommand:
    return MatchCommand(names=tuple(command_of(n) for n in names))


def data(*values: str) -> MatchData:
    return MatchData(values=tuple(values))


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
        case MatchCommand(names=names):
            return command_of(getattr(event, "text", "") or "") in names
        case MatchData(values=values):
            return getattr(event, "data", "") in values
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
