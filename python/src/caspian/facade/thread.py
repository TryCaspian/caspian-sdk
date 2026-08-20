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

    def __init__(self, thread_id: ThreadId, *, sink: Any = None) -> None:
        self.thread_id = thread_id
        self._commands: list[Command] = []
        self._sink = sink

    @property
    def commands(self) -> list[Command]:
        """Commands accumulated during this turn."""
        return list(self._commands)

    def stream(self, *, min_chars: int = 24, throttle: float = 0.5) -> Stream:
        """Open a streaming reply. See Stream.

        Sends chunks as they arrive when the channel supports editing a sent
        message; otherwise buffers and sends once on close. `throttle` caps how
        often an edit goes out, since platforms rate-limit rapid edits.
        """
        return Stream(self, min_chars=min_chars, throttle=throttle)

    def post(self, text: str, *, actions: tuple[Any, ...] = ()) -> None:
        """Enqueue a Post command."""
        self._commands.append(
            Post(thread_id=self.thread_id, text=text, actions=_to_buttons(actions))
        )

    def send(self, text: str, *, actions: tuple[Any, ...] = ()) -> None:
        """Send WITHOUT threading, even mid-conversation.

        post() answers the message that triggered the turn; use this for an
        unprompted message that should start its own thread.
        """
        self._commands.append(
            Post(
                thread_id=self.thread_id,
                text=text,
                actions=_to_buttons(actions),
                standalone=True,
            )
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


class Stream:
    """A reply that is sent while it is still being written.

    Posts the first chunk, then edits that same message as more text arrives,
    which is how a bot appears to "type out" a long answer. Falls back to a
    single Post at the end when the runtime or channel cannot edit, so handler
    code never has to branch on it.

    Use it as a context manager so the final flush always happens::

        with thread.stream() as out:
            for chunk in llm:
                out.append(chunk)
    """

    def __init__(
        self, thread: Thread, *, min_chars: int = 24, throttle: float = 0.5
    ) -> None:
        self._thread = thread
        self._sink = thread._sink
        self._min_chars = min_chars
        # At most one edit every `throttle` seconds. Without it a fast model
        # produces an edit per token, which platforms rate-limit; close() always
        # sends the complete text regardless.
        self._throttle = throttle
        self._last_flush = 0.0
        self._text = ""
        self._sent = ""
        self._message_id = ""
        self._closed = False

    @property
    def text(self) -> str:
        """Everything appended so far."""
        return self._text

    @property
    def live(self) -> bool:
        """True when chunks are being sent as they arrive rather than buffered."""
        return bool(self._sink is not None and getattr(self._sink, "can_stream", False))

    def append(self, chunk: str) -> None:
        """Add text. Flushes once enough has accumulated to be worth a call."""
        if self._closed or not chunk:
            return
        self._text += chunk
        if not self.live:
            return
        if len(self._text) - len(self._sent) < self._min_chars:
            return
        if self._throttle > 0:
            import time

            now = time.monotonic()
            if now - self._last_flush < self._throttle:
                return
            self._last_flush = now
        self._flush()

    def close(self) -> None:
        """Send whatever is left. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if not self._text:
            return
        if self.live:
            self._flush()
        else:
            # Buffered mode: one Post with the whole answer.
            self._thread.post(self._text)

    def _flush(self) -> None:
        if self._text == self._sent:
            return
        if not self._message_id:
            self._message_id = self._sink.emit(
                Post(thread_id=self._thread.thread_id, text=self._text)
            )
            # No id back means we cannot target an edit; buffer from here on.
            if not self._message_id:
                self._sink = None
                self._sent = self._text
                return
        else:
            self._sink.emit(
                Edit(
                    thread_id=self._thread.thread_id,
                    message_id=self._message_id,
                    text=self._text,
                )
            )
        self._sent = self._text

    def __enter__(self) -> Stream:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False
