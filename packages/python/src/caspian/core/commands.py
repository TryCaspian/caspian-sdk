"""Commands — the output of step(). Pure data describing intent, never execution."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from caspian.core.types import Attachment, Block, Button, ThreadId


class Post(BaseModel):
    """Send a message to a thread.

    When the turn was triggered by an inbound message, a runner may deliver
    this as a reply to that message so the conversation threads correctly
    (this is what makes email keep its In-Reply-To chain). Set
    ``standalone=True`` for a message that must start its own thread.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Post"] = "Post"
    thread_id: ThreadId
    text: str
    actions: tuple[Button, ...] = ()
    standalone: bool = False


class Reply(BaseModel):
    """Send a message as a reply to a specific message (threaded)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Reply"] = "Reply"
    thread_id: ThreadId
    reply_to: str
    text: str
    actions: tuple[Button, ...] = ()


class SendBlocks(BaseModel):
    """Send rich blocks (Slack Block Kit, Discord embeds, Telegram-rendered)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["SendBlocks"] = "SendBlocks"
    thread_id: ThreadId
    blocks: tuple[Block, ...]
    text: str = ""
    actions: tuple[Button, ...] = ()


class SendMedia(BaseModel):
    """Send a media attachment (photo, file, audio, video)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["SendMedia"] = "SendMedia"
    thread_id: ThreadId
    attachment: Attachment
    caption: str = ""


class Edit(BaseModel):
    """Edit an existing message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Edit"] = "Edit"
    thread_id: ThreadId
    message_id: str
    text: str
    actions: tuple[Button, ...] = ()


class Delete(BaseModel):
    """Delete a message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Delete"] = "Delete"
    thread_id: ThreadId
    message_id: str


class React(BaseModel):
    """Add a reaction to a message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["React"] = "React"
    thread_id: ThreadId
    message_id: str
    emoji: str


class Typing(BaseModel):
    """Indicate typing/processing."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Typing"] = "Typing"
    thread_id: ThreadId


class Pin(BaseModel):
    """Pin a message in a thread."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Pin"] = "Pin"
    thread_id: ThreadId
    message_id: str


class Unpin(BaseModel):
    """Unpin a message in a thread."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Unpin"] = "Unpin"
    thread_id: ThreadId
    message_id: str


class Forward(BaseModel):
    """Forward a message to another thread."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Forward"] = "Forward"
    from_thread_id: ThreadId
    to_thread_id: ThreadId
    message_id: str


class MarkRead(BaseModel):
    """Mark a thread (or up to a message) as read."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["MarkRead"] = "MarkRead"
    thread_id: ThreadId
    message_id: str = ""


class Initiate(BaseModel):
    """Start a conversation with someone who hasn't messaged first (cold-DM)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Initiate"] = "Initiate"
    thread_id: ThreadId
    text: str
    actions: tuple[Button, ...] = ()


class ScheduleSend(BaseModel):
    """Send a message at a future time (epoch seconds)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["ScheduleSend"] = "ScheduleSend"
    thread_id: ThreadId
    text: str
    send_at: int
    actions: tuple[Button, ...] = ()


class OpenModal(BaseModel):
    """Open an interactive modal on a channel that supports them."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["OpenModal"] = "OpenModal"
    thread_id: ThreadId
    trigger_id: str
    blocks: tuple[Block, ...]
    title: str = ""
    callback_id: str = ""


class UpdateModal(BaseModel):
    """Update an already-open modal."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["UpdateModal"] = "UpdateModal"
    view_id: str
    blocks: tuple[Block, ...]
    title: str = ""


class ListHistory(BaseModel):
    """Pull recent messages from a thread (backfill)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["ListHistory"] = "ListHistory"
    thread_id: ThreadId
    limit: int = 20
    before: str = ""


class Subscribe(BaseModel):
    """Subscribe this thread for future proactive messages."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Subscribe"] = "Subscribe"
    thread_id: ThreadId


class SetState(BaseModel):
    """Set per-thread state."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["SetState"] = "SetState"
    thread_id: ThreadId
    key: str
    value: Any


class Call(BaseModel):
    """Call a native adapter method (e.g. telegram.send_poll). Escape hatch."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Call"] = "Call"
    method: str
    args: dict[str, Any] = Field(default_factory=dict)


class Host(BaseModel):
    """Run the customer's agent function. The handler_id references a registered callable."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    tag: Literal["Host"] = "Host"
    handler_id: str


Command = Annotated[
    Post
    | Reply
    | SendBlocks
    | SendMedia
    | Edit
    | Delete
    | React
    | Typing
    | Pin
    | Unpin
    | Forward
    | MarkRead
    | Initiate
    | ScheduleSend
    | OpenModal
    | UpdateModal
    | ListHistory
    | Subscribe
    | SetState
    | Call
    | Host,
    Field(discriminator="tag"),
]

# Commands that carry a thread_id used for overlap keying / routing.
OUTBOUND_TAGS = frozenset(
    {
        "Post",
        "Reply",
        "SendBlocks",
        "SendMedia",
        "Edit",
        "Delete",
        "React",
        "Typing",
        "Pin",
        "Unpin",
        "Forward",
        "MarkRead",
        "Initiate",
        "ScheduleSend",
        "OpenModal",
        "UpdateModal",
        "ListHistory",
    }
)
