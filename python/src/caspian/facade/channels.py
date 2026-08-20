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
from caspian.provision import ChannelConnection, Channels, ProvisionError, Via


class ChannelManager:
    """Facade-side registry of connected channels.

    Delegates hosted/self-host validation to provision.Channels, resolves the
    adapter from the registry, and bridges to a core Connection.
    """

    def __init__(self, *, gateway_client: Any = None) -> None:  # noqa: ANN401
        self._provision = Channels()
        self._gateway_client = gateway_client
        self._adapters: dict[str, Any] = {}
        self._connections: dict[str, Connection] = {}
        self._records: dict[str, ChannelConnection] = {}

    def add(self, channel: str, **options: Any) -> ChannelConnection:
        """Add a channel. `via` defaults to hosted; self-host requires the secret.

        A local adapter is required only for `via="self-host"`, because that is
        the mode where this process speaks the platform's own protocol. In
        hosted mode the gateway owns the protocol and inbound arrives as a
        gateway event, so any channel the gateway supports works here even
        without a local adapter (bluesky, zulip, gmeet, rcs, ...).

        Raises KeyError if self-host is requested for a channel with no adapter.
        Raises provision.ProvisionError if a required token is missing.
        """
        record = self._provision.add(channel, **options)

        if record.via == Via.SELF_HOST and channel not in REGISTRY:
            raise KeyError(
                f"No adapter for channel {channel!r}, so it cannot be self-hosted. "
                f"Self-host supports: {', '.join(sorted(REGISTRY))}. "
                f"Use via='hosted' to let the gateway handle {channel!r}."
            )

        if record.via == Via.HOSTED and self._gateway_client is not None:
            from caspian.hosted.provisioning import HostedProvisioning

            provisioned = HostedProvisioning(self._gateway_client).add_connection(
                channel, options
            )
            # Swallowing this made a failed connect look successful: the local
            # record still said "added" while the gateway had nothing.
            if not provisioned.is_ok:
                raise ProvisionError(
                    f"Gateway refused to connect {channel!r}: {provisioned.error}"
                )

        # Hosted-only channels have no local adapter; that is expected.
        self._adapters[channel] = get_adapter(channel) if channel in REGISTRY else None
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
        adapter = self._adapters[channel]
        if adapter is None:
            raise KeyError(
                f"Channel {channel!r} is hosted-only (no local adapter). Its inbound "
                f"arrives as a gateway event: call cx.handle('gateway', body, headers) "
                f"or cx.run(), not cx.handle({channel!r}, ...)."
            )
        return adapter

    def self_hostable(self, channel: str) -> bool:
        """True if this channel can run without the gateway (a local adapter exists)."""
        return channel in REGISTRY

    def connection_for(self, channel: str) -> Connection:
        if channel not in self._connections:
            raise KeyError(f"Channel {channel!r} was not added; call channels.add() first")
        return self._connections[channel]

    def inbound_owner(self, channel: str) -> str:
        if channel not in self._records:
            raise KeyError(f"Channel {channel!r} was not added; call channels.add() first")
        return self._records[channel].inbound_owner

    def added(self) -> list[str]:
        """Names of channels that have been added."""
        return list(self._connections)

    def list(self) -> list[ChannelConnection]:
        """All provisioned connection records."""
        return self._provision.list()
