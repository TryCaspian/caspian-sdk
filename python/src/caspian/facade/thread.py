"""Thread — the handle passed to user handlers. Enqueues Commands, never calls HTTP."""

from __future__ import annotations

from typing import Any

from caspian.core.commands import (
    Command,
    Delete,
    Edit,
    Forward,
    Initiate,
    ListHistory,
    MarkRead,
    Pin,
    Post,
    React,
    Reply,
    ScheduleSend,
    SendBlocks,
    SendMedia,
    SetState,
    Subscribe,
    Typing,
    Unpin,
)
from caspian.core.types import Attachment, Block, Button, ThreadId


def _to_buttons(actions: tuple[Any, ...]) -> tuple[Button, ...]:
    """Coerce dicts or Buttons into Button instances (ergonomic public surface)."""
    out: list[Button] = []
    for a in actions:
        if isinstance(a, Button):
            out.append(a)
        elif isinstance(a, dict):
            out.append(
                Button(
                    label=a.get("label", a.get("text", "")),
                    data=a.get("data", a.get("value", "")),
                    url=a.get("url", ""),
                    style=a.get("style", "default"),
                )
            )
    return tuple(out)


class Thread:
    """User-facing thread handle. Methods enqueue Commands into the turn's command list.

    This is NOT an HTTP client. It builds intent as data.
    """

    def __init__(self, thread_id: ThreadId) -> None:
        self.thread_id = thread_id
        self._commands: list[Command] = []

    @property
    def commands(self) -> list[Command]:
        """Commands accumulated during this turn."""
        return list(self._commands)

    def post(self, text: str, *, actions: tuple[Any, ...] = ()) -> None:
        """Enqueue a Post command."""
        self._commands.append(
            Post(thread_id=self.thread_id, text=text, actions=_to_buttons(actions))
        )

    def reply(self, reply_to: str, text: str, *, actions: tuple[Any, ...] = ()) -> None:
        """Enqueue a Reply command (threaded reply to a specific message)."""
        self._commands.append(
            Reply(
                thread_id=self.thread_id,
                reply_to=reply_to,
                text=text,
                actions=_to_buttons(actions),
            )
        )

    def send_blocks(
        self, blocks: tuple[Block, ...], *, text: str = "", actions: tuple[Any, ...] = ()
    ) -> None:
        """Enqueue a SendBlocks command (rich layout)."""
        self._commands.append(
            SendBlocks(
                thread_id=self.thread_id,
                blocks=blocks,
                text=text,
                actions=_to_buttons(actions),
            )
        )

    def send_media(self, attachment: Attachment, *, caption: str = "") -> None:
        """Enqueue a SendMedia command."""
        self._commands.append(
            SendMedia(thread_id=self.thread_id, attachment=attachment, caption=caption)
        )

    def typing(self) -> None:
        """Enqueue a Typing indicator command."""
        self._commands.append(Typing(thread_id=self.thread_id))

    def edit(self, message_id: str, text: str, *, actions: tuple[Any, ...] = ()) -> None:
        """Enqueue an Edit command."""
        self._commands.append(
            Edit(
                thread_id=self.thread_id,
                message_id=message_id,
                text=text,
                actions=_to_buttons(actions),
            )
        )

    def delete(self, message_id: str) -> None:
        """Enqueue a Delete command."""
        self._commands.append(Delete(thread_id=self.thread_id, message_id=message_id))

    def react(self, message_id: str, emoji: str) -> None:
        """Enqueue a React command."""
        self._commands.append(
            React(thread_id=self.thread_id, message_id=message_id, emoji=emoji)
        )

    def pin(self, message_id: str) -> None:
        """Enqueue a Pin command."""
        self._commands.append(Pin(thread_id=self.thread_id, message_id=message_id))

    def unpin(self, message_id: str) -> None:
        """Enqueue an Unpin command."""
        self._commands.append(Unpin(thread_id=self.thread_id, message_id=message_id))

    def forward(self, to_thread_id: ThreadId, message_id: str) -> None:
        """Enqueue a Forward command."""
        self._commands.append(
            Forward(
                from_thread_id=self.thread_id,
                to_thread_id=to_thread_id,
                message_id=message_id,
            )
        )

    def mark_read(self, message_id: str = "") -> None:
        """Enqueue a MarkRead command."""
        self._commands.append(MarkRead(thread_id=self.thread_id, message_id=message_id))

    def initiate(self, text: str, *, actions: tuple[Any, ...] = ()) -> None:
        """Enqueue an Initiate command (cold-DM)."""
        self._commands.append(
            Initiate(thread_id=self.thread_id, text=text, actions=_to_buttons(actions))
        )

    def schedule(self, text: str, send_at: int, *, actions: tuple[Any, ...] = ()) -> None:
        """Enqueue a ScheduleSend command."""
        self._commands.append(
            ScheduleSend(
                thread_id=self.thread_id,
                text=text,
                send_at=send_at,
                actions=_to_buttons(actions),
            )
        )

    def history(self, *, limit: int = 20, before: str = "") -> None:
        """Enqueue a ListHistory command (backfill)."""
        self._commands.append(
            ListHistory(thread_id=self.thread_id, limit=limit, before=before)
        )

    def subscribe(self) -> None:
        """Enqueue a Subscribe command."""
        self._commands.append(Subscribe(thread_id=self.thread_id))

    def set_state(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Enqueue a SetState command."""
        self._commands.append(SetState(thread_id=self.thread_id, key=key, value=value))
