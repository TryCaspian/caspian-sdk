"""Ports — Protocol declarations for adapters, host, and state.

Core imports these. Implementations live outside core.
"""

from __future__ import annotations

from typing import Any, Generic, Protocol

from typing_extensions import TypeVar

from caspian.connection import Connection
from caspian.core.commands import Command
from caspian.core.errors import CaspianError
from caspian.core.types import Event, ThreadId

T = TypeVar("T", default=Any)


class Result(Generic[T]):
    """Ok(value) or Err(error). Never both.

    After ``execute``, ``value`` is a ``Sent`` (a planned call). After ``parse``,
    it is a list of Events. On failure ``error`` is a CaspianError and
    ``is_ok`` is False — do not read ``value``.
    """

    def __init__(self, value: T | None = None, error: CaspianError | None = None) -> None:
        self._value = value
        self._error = error

    @property
    def is_ok(self) -> bool:
        return self._error is None

    @property
    def value(self) -> T:
        return self._value  # type: ignore[return-value]

    @property
    def error(self) -> CaspianError | None:
        return self._error

    @classmethod
    def ok(cls, value: T | None = None) -> Result[T]:
        return cls(value=value)

    @classmethod
    def err(cls, error: CaspianError) -> Result[T]:
        return cls(error=error)


class Headers(dict):
    """HTTP headers, looked up without regard to case.

    RFC 9110 makes field names case insensitive, and web frameworks disagree
    about what they hand you: Starlette's `dict(request.headers)`, Express and
    Bun lowercase them, while http.server and Flask preserve whatever the
    client sent. Adapters ask for "X-Slack-Signature"; without this, every
    signature check silently fails against half the ecosystem and each request
    is rejected as unverified.

    Original casing is preserved on iteration so raw payloads still round-trip.
    """

    def __init__(self, source: dict[str, str] | None = None) -> None:
        super().__init__(source or {})
        self._folded = {k.lower(): v for k, v in self.items()}

    def __getitem__(self, key: str) -> str:
        try:
            return self._folded[key.lower()]
        except KeyError:
            raise KeyError(key) from None

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        return self._folded.get(key.lower(), default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._folded

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._folded[key.lower()] = value


class RawInbound:
    """Raw inbound bytes from a platform webhook. Opaque to core."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = Headers(headers)


class Sent:
    """A planned outbound call, or the confirmation after transport dispatched it.

    ``raw`` describes the call (transport, url, json, …). Adapters fill this;
    HttpTransport reads it. ``message_id`` is set after a successful dispatch.
    """

    def __init__(self, message_id: str = "", raw: dict[str, Any] | None = None) -> None:
        self.message_id = message_id
        self.raw = raw or {}


class AdapterPort(Protocol):
    """Port: translate between platform wire format and kernel Events/Commands.

    Adapters are a codec: parse inbound, plan outbound. Transport sends.
    """

    @property
    def name(self) -> str: ...

    def parse(self, raw: RawInbound) -> Result: ...

    def execute(self, cmd: Command, conn: Connection) -> Result: ...

    def verify(self, raw: RawInbound, conn: Connection) -> bool: ...

    def overlap_key(self, event: Event) -> str: ...

    def capabilities(self) -> frozenset[str]: ...

    def format(self, text: str) -> str: ...

    def encode_thread(self, *parts: str) -> ThreadId: ...

    def decode_thread(self, thread_id: ThreadId) -> str | tuple[str, ...]: ...


class TransportPort(Protocol):
    """Port: perform the effect described by a Sent.

    Adapters build request-descriptions (pure). A transport reads `sent.raw`
    and dispatches — HTTP, SMTP, TwiML, gateway, or a test fake. One method,
    one fake.
    """

    def dispatch(self, sent: Sent) -> Result: ...


class StreamSink(Protocol):
    """Port: send one Command *during* a handler and report the message id it made.

    Normally a handler runs to completion and its Commands are sent afterwards.
    Streaming a long answer needs the opposite: post the first chunk, then edit
    that message as more text arrives. This port is that escape hatch, and it is
    the only place where an effect happens mid-handler. Core stays pure: it just
    declares the shape.

    `can_stream` is False when the runtime cannot support it (no transport, or a
    channel that cannot edit a sent message); callers must then buffer and send
    once at the end instead.
    """

    can_stream: bool

    def emit(self, command: Command) -> str: ...


class HostPort(Protocol):
    """Port: the customer's agent. Returns Commands; never talks to a platform."""

    def run(
        self,
        handler_id: str,
        event: Event,
        *,
        skipped_count: int = 0,
        sink: StreamSink | None = None,
    ) -> list[Command]: ...


class StatePort(Protocol):
    """Port: per-thread state storage (relationship memory)."""

    async def get(self, thread_id: ThreadId, key: str) -> Any: ...  # noqa: ANN401

    async def set(self, thread_id: ThreadId, key: str, value: Any) -> None: ...  # noqa: ANN401

    async def recent(self, thread_id: ThreadId, limit: int = 20) -> list[Event]: ...
