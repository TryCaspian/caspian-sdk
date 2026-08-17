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

# ─── Shared content types (media + rich blocks) ─────────────────────────────


class Attachment(BaseModel):
    """A media attachment (photo, file, audio, video). Channel-agnostic."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["photo", "file", "audio", "video", "sticker", "voice"]
    url: str = ""
    file_id: str = ""
    filename: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    caption: str = ""


class Block(BaseModel):
    """A rich layout block (Slack Block Kit, Discord embed, Telegram-rendered).

    `type` is the block kind (section, image, divider, actions, header, ...).
    `content` carries the block-specific fields. Adapters render this to their
    native format via format().
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str
    content: dict[str, Any] = Field(default_factory=dict)


class Button(BaseModel):
    """An abstract button. Adapters render to inline keyboards / actions / components."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    label: str
    data: str = ""
    url: str = ""
    style: Literal["default", "primary", "danger"] = "default"


# ─── Events (inbound from adapter.parse) ─────────────────────────────────────


class Message(BaseModel):
    """A text/media message from a user."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["message"] = "message"
    thread_id: ThreadId
    text: str
    chat_kind: ChatKind
    sender: str = ""
    message_id: str = ""
    attachments: tuple[Attachment, ...] = ()
    blocks: tuple[Block, ...] = ()
    reply_to: str = ""
    topic_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    """A button press / callback / interaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["action"] = "action"
    thread_id: ThreadId
    data: str
    sender: str = ""
    message_id: str = ""
    interaction_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class Reaction(BaseModel):
    """An emoji reaction added or removed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["reaction"] = "reaction"
    thread_id: ThreadId
    emoji: str
    sender: str = ""
    message_id: str = ""
    removed: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class Receipt(BaseModel):
    """A read / delivered confirmation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["receipt"] = "receipt"
    thread_id: ThreadId
    status: Literal["read", "delivered"]
    sender: str = ""
    message_id: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class MemberJoin(BaseModel):
    """A member joined the conversation/group."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["member_join"] = "member_join"
    thread_id: ThreadId
    member: str
    chat_kind: ChatKind = "group"
    raw: dict[str, Any] = Field(default_factory=dict)


class MemberLeave(BaseModel):
    """A member left the conversation/group."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["member_leave"] = "member_leave"
    thread_id: ThreadId
    member: str
    chat_kind: ChatKind = "group"
    raw: dict[str, Any] = Field(default_factory=dict)


class Edited(BaseModel):
    """A user edited their own message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["edited"] = "edited"
    thread_id: ThreadId
    message_id: str
    text: str
    sender: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class Deleted(BaseModel):
    """A user deleted their message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["deleted"] = "deleted"
    thread_id: ThreadId
    message_id: str
    sender: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


Event = Annotated[
    Message
    | Action
    | Reaction
    | Receipt
    | MemberJoin
    | MemberLeave
    | Edited
    | Deleted,
    Field(discriminator="kind"),
]

EventKind = Literal[
    "message",
    "action",
    "reaction",
    "receipt",
    "member_join",
    "member_leave",
    "edited",
    "deleted",
]

# ─── Overlap policy ──────────────────────────────────────────────────────────


class OverlapPolicy(StrEnum):
    QUEUE = "queue"
    DEBOUNCE = "debounce"
    DROP = "drop"
    PARALLEL = "parallel"
    STREAM = "stream"


class Overlap(BaseModel):
    """Overlap configuration for a rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    policy: OverlapPolicy = OverlapPolicy.QUEUE
    bound: int = 16


# ─── Rule & App ──────────────────────────────────────────────────────────────

from caspian.core.predicates import Predicate  # noqa: E402


class Rule(BaseModel):
    """A single rule: when (predicate) → how to overlap → what to do (commands)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    predicate: Predicate
    overlap: Overlap = Overlap()
    handler_id: str = ""
    # Sent the moment a matching event arrives, before the handler runs. The
    # point is channels with no typing indicator (email, SMS, X): without it a
    # human waits on silence while the agent thinks. Empty means no ack.
    ack: str = ""


class App(BaseModel):
    """The program: a list of rules. Inspectable, serializable, testable."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    rules: tuple[Rule, ...] = ()
