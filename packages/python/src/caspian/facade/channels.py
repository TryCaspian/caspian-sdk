"""ChannelManager — the composition seam between provisioning, adapters, and the runtime.

This lives in the facade (B surface): it is allowed to import core, adapters, and
provision. Paperwork failures raise the tagged error itself (usually ProvisionError).
handle()/listen() stay Result-shaped for per-event failure.
"""

from __future__ import annotations

from typing import Any

from caspian.adapters import REGISTRY, get_adapter
from caspian.connection import Connection, Via, overlay_remote
from caspian.core.errors import ProvisionError
from caspian.core.ports import AdapterPort
from caspian.provision import Channels


class ChannelManager:
    """Facade-side registry of connected channels.

    Delegates hosted/self-host validation to provision.Channels, resolves the
    adapter from the registry, and keeps one Connection per channel.
    """

    def __init__(self, *, gateway_client: Any = None) -> None:  # noqa: ANN401
        self._provision = Channels()
        self._gateway_client = gateway_client
        self._adapters: dict[str, AdapterPort | None] = {}
        self._connections: dict[str, Connection] = {}

    def add(
        self,
        channel: str,
        *,
        via: str = "hosted",
        display_name: str = "",
        bot_token: str = "",
        webhook_url: str = "",
        inbound: bool = True,
        webhook_secret: str = "",
        signing_secret: str = "",
        app_secret: str = "",
        api_key: str = "",
        **kwargs: Any,
    ) -> Connection:
        """Connect a channel. ``via`` defaults to hosted; self-host needs the secret.

        Args:
            channel: Catalog name (telegram, slack, …) or a hosted-only name
                the gateway speaks (bluesky, …).
            via: ``hosted`` (default) or ``self-host``.
            bot_token: Telegram always; Slack/Discord self-host. Hosted does
                not mint a bot.
            webhook_url: Public URL for self-host webhooks (Twilio also uses
                this in the signature).
            webhook_secret: Telegram secret token header; Linear/iMessage HMAC.
            signing_secret: Slack request signing secret.
            app_secret: Meta HMAC (WhatsApp, Messenger).
            api_key: Linear GraphQL, iMessage relay, etc.

        A local adapter is required only for ``via="self-host"``. Hosted inbound
        arrives as a gateway event, so any channel the gateway supports works
        even without a local adapter.

        Raises ProvisionError (or another CaspianError from the gateway) if
        paperwork or the gateway refuses.
        """
        built = self._provision.add(
            channel,
            via=via,
            display_name=display_name,
            bot_token=bot_token,
            webhook_url=webhook_url,
            inbound=inbound,
            webhook_secret=webhook_secret,
            signing_secret=signing_secret,
            app_secret=app_secret,
            api_key=api_key,
            **kwargs,
        )
        if isinstance(built, str):
            raise ProvisionError(reason=built)
        conn = built

        if conn.via == Via.SELF_HOST and channel not in REGISTRY:
            raise ProvisionError(
                reason=(
                    f"No adapter for channel {channel!r}, so it cannot be self-hosted. "
                    f"Self-host supports: {', '.join(sorted(REGISTRY))}. "
                    f"Use via='hosted' to let the gateway handle {channel!r}."
                )
            )

        if conn.via == Via.HOSTED and self._gateway_client is not None:
            from caspian.hosted.provisioning import HostedProvisioning

            options: dict[str, Any] = {
                "via": via,
                "display_name": display_name,
                "bot_token": bot_token,
                "webhook_url": webhook_url,
                "inbound": inbound,
                "webhook_secret": webhook_secret,
                "signing_secret": signing_secret,
                "app_secret": app_secret,
                "api_key": api_key,
                **kwargs,
            }
            provisioned = HostedProvisioning(self._gateway_client).add_connection(
                channel, options
            )
            if not provisioned.is_ok:
                err = provisioned.error or ProvisionError(reason="gateway refused")
                raise err
            conn = overlay_remote(conn, provisioned.value)

        if not conn.id:
            conn.id = f"{channel}:{len(self._connections)}"

        # Hosted-only channels have no local adapter; that is expected.
        self._adapters[channel] = get_adapter(channel) if channel in REGISTRY else None
        self._connections[channel] = conn
        return conn

    def adapter_for(self, channel: str) -> AdapterPort:
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
