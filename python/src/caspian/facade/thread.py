"""Thread — the handle passed to user handlers. Enqueues Commands, never calls HTTP."""

from __future__ import annotations

from typing import Any

from caspian.core.commands import Command, Edit, Post, React, SetState, Subscribe, Typing
from caspian.core.types import ThreadId


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

    def post(self, text: str, *, actions: tuple[dict[str, Any], ...] = ()) -> None:
        """Enqueue a Post command."""
        self._commands.append(Post(thread_id=self.thread_id, text=text, actions=actions))

    def typing(self) -> None:
        """Enqueue a Typing indicator command."""
        self._commands.append(Typing(thread_id=self.thread_id))

    def edit(self, message_id: str, text: str) -> None:
        """Enqueue an Edit command."""
        self._commands.append(Edit(thread_id=self.thread_id, message_id=message_id, text=text))

    def react(self, message_id: str, emoji: str) -> None:
        """Enqueue a React command."""
        self._commands.append(React(thread_id=self.thread_id, message_id=message_id, emoji=emoji))

    def subscribe(self) -> None:
        """Enqueue a Subscribe command."""
        self._commands.append(Subscribe(thread_id=self.thread_id))

    def set_state(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Enqueue a SetState command."""
        self._commands.append(SetState(thread_id=self.thread_id, key=key, value=value))
