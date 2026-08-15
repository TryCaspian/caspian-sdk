"""Ports — Protocol declarations for adapters, host, and state.

Core imports these. Implementations live outside core.
"""

from __future__ import annotations

from typing import Any, Protocol

from caspian.core.commands import Command
from caspian.core.errors import CaspianError
from caspian.core.types import ConnectionId, Event, ThreadId


class Result:
    """Simple Result type for core. Ok or Err."""

    def __init__(self, value: Any = None, error: CaspianError | None = None) -> None:  # noqa: ANN401
        self._value = value
        self._error = error

    @property
    def is_ok(self) -> bool:
        return self._error is None

    @property
    def value(self) -> Any:  # noqa: ANN401
        return self._value

    @property
    def error(self) -> CaspianError | None:
        return self._error

    @classmethod
    def ok(cls, value: Any = None) -> Result:  # noqa: ANN401
        return cls(value=value)

    @classmethod
    def err(cls, error: CaspianError) -> Result:
        return cls(error=error)


class RawInbound:
    """Raw inbound bytes from a platform webhook. Opaque to core."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}


class Sent:
    """Confirmation that a command was executed."""

    def __init__(self, message_id: str = "", raw: dict[str, Any] | None = None) -> None:
        self.message_id = message_id
        self.raw = raw or {}


class Connection:
    """A provisioned channel connection. Opaque config for the adapter."""

    def __init__(
        self,
        id: ConnectionId,
        channel: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.channel = channel
        self.config = config or {}


class AdapterPort(Protocol):
    """Port: translate between platform wire format and kernel Events/Commands."""

    @property
    def name(self) -> str: ...

    def parse(self, raw: RawInbound) -> Result: ...

    def execute(self, cmd: Command, conn: Connection) -> Result: ...

    def overlap_key(self, event: Event) -> str: ...

    def capabilities(self) -> frozenset[str]: ...


class HostPort(Protocol):
    """Port: the customer's agent function."""

    async def run(
        self, handler_id: str, event: Event, *, skipped_count: int = 0
    ) -> list[Command]: ...


class StatePort(Protocol):
    """Port: per-thread state storage (relationship memory)."""

    async def get(self, thread_id: ThreadId, key: str) -> Any: ...  # noqa: ANN401

    async def set(self, thread_id: ThreadId, key: str, value: Any) -> None: ...  # noqa: ANN401

    async def recent(self, thread_id: ThreadId, limit: int = 20) -> list[Event]: ...
