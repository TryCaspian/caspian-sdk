"""Assemble an AdapterPort from parse + plan. Ceremony comes from the catalog."""

from __future__ import annotations

from collections.abc import Callable

from caspian.adapters.thread import decode_thread as _decode
from caspian.adapters.thread import encode_thread as _encode
from caspian.catalog import ChannelName, capabilities_of
from caspian.connection import Connection
from caspian.core.commands import Command, Edit, Initiate, Post, Reply, ScheduleSend, SendBlocks
from caspian.core.ports import RawInbound, Result, Sent
from caspian.core.types import Event, ThreadId

Parse = Callable[[RawInbound], Result]
Plan = Callable[[Command, Connection], Result]
Verify = Callable[[RawInbound, Connection], bool]
Format = Callable[[str], str]
Ack = Callable[[Event, Connection], Result | None]
Poll = Callable[[int, Connection], Result]
Webhook = Callable[[Connection], Result]
Socket = Callable[[Connection], Result]
Encode = Callable[..., ThreadId]
Decode = Callable[[ThreadId], str | tuple[str, ...]]
PostedId = Callable[[Sent], str]

_TEXT_COMMANDS = (Post, Reply, Edit, Initiate, ScheduleSend, SendBlocks)


def from_response(*keys: str) -> PostedId:
    """Read a posted-message id from ``sent.raw['response']`` along ``keys``.

    Telegram: ``from_response("result", "message_id")``.
    Slack: ``from_response("ts")``. Discord: ``from_response("id")``.
    """

    def read(sent: Sent) -> str:
        cur: object = sent.raw.get("response")
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                return ""
            cur = cur[key]
        return "" if cur is None else str(cur)

    return read


def _identity(text: str) -> str:
    return text


def _with_formatted_text(cmd: Command, fmt: Format) -> Command:
    if fmt is _identity or not isinstance(cmd, _TEXT_COMMANDS):
        return cmd
    return cmd.model_copy(update={"text": fmt(cmd.text)})


def pack(
    *,
    channel: ChannelName,
    parse: Parse,
    plan: Plan,
    verify: Verify,
    format: Format | None = None,
    encode_thread: Encode | None = None,
    decode_thread: Decode | None = None,
    acknowledge: Ack | None = None,
    poll: Poll | None = None,
    webhook: Webhook | None = None,
    socket: Socket | None = None,
    posted_id: PostedId | None = None,
) -> type:
    """Return an AdapterPort class. Overlap, capabilities, and thread codec are defaults."""
    fmt = format or _identity
    encode = encode_thread or (lambda *parts: _encode(channel, *parts))
    decode = decode_thread or _decode
    posted = posted_id or (lambda _sent: "")

    class Packed:
        name = channel

        def parse(self, raw: RawInbound) -> Result:
            return parse(raw)

        def execute(self, cmd: Command, conn: Connection) -> Result:
            return plan(_with_formatted_text(cmd, fmt), conn)

        def verify(self, raw: RawInbound, conn: Connection) -> bool:
            return verify(raw, conn)

        def overlap_key(self, event: Event) -> str:
            return str(event.thread_id)

        def capabilities(self) -> frozenset[str]:
            return capabilities_of(channel)

        def format(self, text: str) -> str:
            return fmt(text)

        def encode_thread(self, *parts: str) -> ThreadId:
            return encode(*parts)

        def decode_thread(self, thread_id: ThreadId) -> str | tuple[str, ...]:
            return decode(thread_id)

        def acknowledge(self, event: Event, conn: Connection) -> Result | None:
            if acknowledge is None:
                return None
            return acknowledge(event, conn)

        def poll(self, offset: int, conn: Connection) -> Result:
            if poll is None:
                from caspian.core.errors import AdapterError

                return Result.err(AdapterError(reason=f"{channel} does not poll"))
            return poll(offset, conn)

        def webhook(self, conn: Connection) -> Result:
            if webhook is None:
                return Result.ok(Sent(raw={"transport": "noop", "native": ""}))
            return webhook(conn)

        def socket(self, conn: Connection) -> Result:
            if socket is None:
                from caspian.core.errors import AdapterError

                return Result.err(
                    AdapterError(reason=f"{channel} does not listen on a socket")
                )
            return socket(conn)

        def posted_id(self, sent: Sent) -> str:
            return posted(sent)

    Packed.__name__ = f"{channel.capitalize()}Adapter"
    Packed.__qualname__ = Packed.__name__
    Packed.__module__ = "caspian.adapters.pack"
    return Packed
