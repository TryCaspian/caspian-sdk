"""ChannelManager — the composition seam between provisioning, adapters, and the runtime.

This lives in the facade (B surface): it is allowed to import core, adapters, and
provision. It turns `cx.channels.add("telegram", ...)` into three things a runner
needs: a validated connection record (provision), the channel's adapter (registry),
and a core `Connection` the interpreter/adapter close over.
"""

from __future__ import annotations

from typing import Any

from caspian.adapters import REGISTRY, get_adapter
from caspian.core.ports import Connection
from caspian.core.types import ConnectionId
from caspian.provision import ChannelConnection, Channels


class ChannelManager:
    """Facade-side registry of connected channels.

    Delegates hosted/self-host validation to provision.Channels, resolves the
    adapter from the registry, and bridges to a core Connection.
    """

    def __init__(self) -> None:
        self._provision = Channels()
        self._adapters: dict[str, Any] = {}
        self._connections: dict[str, Connection] = {}
        self._records: dict[str, ChannelConnection] = {}

    def add(self, channel: str, **options: Any) -> ChannelConnection:
        """Add a channel. `via` defaults to hosted; self-host requires the secret.

        Raises KeyError (clear message) if no adapter exists for the channel.
        """
        if channel not in REGISTRY:
            raise KeyError(
                f"No adapter for channel {channel!r}. "
                f"Known channels: {', '.join(sorted(REGISTRY))}"
            )

        record = self._provision.add(channel, **options)
        self._adapters[channel] = get_adapter(channel)
        self._connections[channel] = Connection(
            id=ConnectionId(f"{channel}:{len(self._connections)}"),
            channel=channel,
            config=record.config,
        )
        self._records[channel] = record
        return record

    def adapter_for(self, channel: str) -> Any:
        if channel not in self._adapters:
            raise KeyError(f"Channel {channel!r} was not added; call channels.add() first")
        return self._adapters[channel]

    def connection_for(self, channel: str) -> Connection:
        if channel not in self._connections:
            raise KeyError(f"Channel {channel!r} was not added; call channels.add() first")
        return self._connections[channel]

    def added(self) -> list[str]:
        """Names of channels that have been added."""
        return list(self._connections)

    def list(self) -> list[ChannelConnection]:
        """All provisioned connection records."""
        return self._provision.list()
