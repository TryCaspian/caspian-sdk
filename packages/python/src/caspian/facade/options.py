"""Typed options for on_message / on_action. Completes keys in the editor."""

from __future__ import annotations

from typing import Literal, TypedDict

from caspian.core.types import ChatKind


class OnMessageOptions(TypedDict, total=False):
    """Filters and overlap for a message handler.

    ``channel`` is a catalog name (telegram, slack, …) or a list of names.
    ``kind`` is dm / group / channel. ``overlap`` is the concurrent-turn policy.
    ``ack`` is an instant reply sent before the handler runs (email, SMS, X).
    """

    channel: str | list[str]
    kind: ChatKind
    overlap: Literal["queue", "debounce", "drop", "parallel", "stream"]
    bound: int
    ack: str


class OnActionOptions(TypedDict, total=False):
    """Filters and overlap for a button / callback handler."""

    channel: str | list[str]
    overlap: Literal["queue", "debounce", "drop", "parallel", "stream"]
    bound: int
    ack: str
