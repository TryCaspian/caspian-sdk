"""ChannelManager — the composition seam between provisioning, adapters, and the runtime.

This lives in the facade (B surface): it is allowed to import core, adapters, and
provision. It turns `cx.channels.add("telegram", ...)` into a Result wrapping a
domain Connection (or a tagged ProvisionError). Nothing here raises for paperwork.
"""

from __future__ import annotations

from typing import Any

from caspian.adapters import REGISTRY, get_adapter
from caspian.connection import Connection, Via, overlay_remote
from caspian.core.errors import ProvisionError
from caspian.core.ports import Result
from caspian.provision import Channels


class ChannelManager:
    """Facade-side registry of connected channels.

    Delegates hosted/self-host validation to provision.Channels, resolves the
    adapter from the registry, and keeps one Connection per channel.
    """

    def __init__(self, *, gateway_client: Any = None) -> None:  # noqa: ANN401
        self._provision = Channels()
        self._gateway_client = gateway_client
        self._adapters: dict[str, Any] = {}
        self._connections: dict[str, Connection] = {}

    def add(self, channel: str, **options: Any) -> Result:
        """Add a channel. `via` defaults to hosted; self-host requires the secret.

        A local adapter is required only for `via="self-host"`, because that is
        the mode where this process speaks the platform's own protocol. In
        hosted mode the gateway owns the protocol and inbound arrives as a
        gateway event, so any channel the gateway supports works here even
        without a local adapter (bluesky, zulip, gmeet, rcs, ...).

        Returns Result.ok(Connection) or Result.err(ProvisionError).
        """
        built = self._provision.add(channel, **options)
        if isinstance(built, str):
            return Result.err(ProvisionError(reason=built))
        conn = built

        if conn.via == Via.SELF_HOST and channel not in REGISTRY:
            return Result.err(
                ProvisionError(
                    reason=(
                        f"No adapter for channel {channel!r}, so it cannot be self-hosted. "
                        f"Self-host supports: {', '.join(sorted(REGISTRY))}. "
                        f"Use via='hosted' to let the gateway handle {channel!r}."
                    )
                )
            )

        if conn.via == Via.HOSTED and self._gateway_client is not None:
            from caspian.hosted.provisioning import HostedProvisioning

            provisioned = HostedProvisioning(self._gateway_client).add_connection(
                channel, options
            )
            # Swallowing this made a failed connect look successful: the local
            # record still said "added" while the gateway had nothing.
            if not provisioned.is_ok:
                return provisioned
            conn = overlay_remote(conn, provisioned.value)

        if not conn.id:
            conn.id = f"{channel}:{len(self._connections)}"

        # Hosted-only channels have no local adapter; that is expected.
        self._adapters[channel] = get_adapter(channel) if channel in REGISTRY else None
        self._connections[channel] = conn
        return Result.ok(conn)

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
        return self.connection_for(channel).inbound_owner

    def added(self) -> list[str]:
        """Names of channels that have been added."""
        return list(self._connections)

    def list(self) -> list[Connection]:
        """All provisioned connection records."""
        return list(self._connections.values())
