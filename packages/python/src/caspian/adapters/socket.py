"""Socket inbound — adapters unwrap frames; the interpreter holds the connection.

One decision type, one driver port. Discord IDENTIFY and Slack envelope-ack are
driver methods, not sibling runners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from caspian.core.ports import RawInbound, Result, Sent


@dataclass(frozen=True, slots=True)
class SocketDecision:
    """What to do with one received frame. Send runs before sink."""

    sink: RawInbound | None = None
    send: tuple[str, ...] = ()
    reconnect: bool = False
    fatal: str = ""
    heartbeat_interval: float | None = None


@dataclass(frozen=True, slots=True)
class SocketUrl:
    """Result of turning the open-plan response into a WebSocket URL."""

    url: str = ""
    fatal: str = ""


class SocketDriver(Protocol):
    """Pure platform socket. No network — the session dispatches and recvs."""

    def open_plan(self) -> Result: ...

    def url_of(self, sent: Sent) -> SocketUrl: ...

    def on_frame(self, frame: dict[str, Any]) -> SocketDecision: ...

    def heartbeat_payload(self) -> str | None: ...

    def connect_kwargs(self) -> dict[str, Any]: ...
