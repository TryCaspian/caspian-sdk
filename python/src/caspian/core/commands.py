"""Commands — the output of step(). Pure data describing intent, never execution."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from caspian.core.types import ThreadId


class Post(BaseModel):
    """Send a message to a thread."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Post"] = "Post"
    thread_id: ThreadId
    text: str
    actions: tuple[dict[str, Any], ...] = ()


class Edit(BaseModel):
    """Edit an existing message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["Edit"] = "Edit"
    thread_id: ThreadId
    message_id: str
    text: str


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
    """Call a native adapter method (e.g. telegram.send_photo)."""

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
    Post | Edit | React | Typing | Subscribe | SetState | Call | Host,
    Field(discriminator="tag"),
]
