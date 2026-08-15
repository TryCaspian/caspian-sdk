"""Provisioning — channels.add, hosted default, self-host opt-in.

This module CANNOT import caspian.core (enforced by import-linter).
It is paperwork, not behavior.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Via(StrEnum):
    HOSTED = "hosted"
    SELF_HOST = "self-host"


class ConnectionStatus(StrEnum):
    REQUESTED = "requested"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    ERROR = "error"


class ChannelConnection:
    """A provisioned channel connection."""

    def __init__(
        self,
        *,
        channel: str,
        via: Via = Via.HOSTED,
        status: ConnectionStatus = ConnectionStatus.REQUESTED,
        address: str = "",
        authorize_url: str = "",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.channel = channel
        self.via = via
        self.status = status
        self.address = address
        self.authorize_url = authorize_url
        self.config = config or {}


class ProvisionError(Exception):
    """Raised when provisioning fails (missing secret, invalid token, etc.)."""


class Channels:
    """Manages channel connections. One verb: add().

    Hosted is the default. Omitting `via` never means 'I forgot a token'.
    Self-host without the required secret is an error.
    """

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._connections: list[ChannelConnection] = []

    @property
    def connections(self) -> list[ChannelConnection]:
        return list(self._connections)

    def add(
        self,
        channel: str,
        *,
        via: str = "hosted",
        display_name: str = "",
        bot_token: str = "",
        webhook_url: str = "",
        inbound: bool = True,
        **kwargs: Any,
    ) -> ChannelConnection:
        """Add a channel connection.

        Default is hosted. `via="self-host"` requires bot_token.
        """
        via_enum = Via(via)

        if via_enum == Via.SELF_HOST and not bot_token:
            raise ProvisionError(
                f"Self-host '{channel}' requires bot_token. "
                "Omit `via` for hosted (Caspian owns the identity)."
            )

        config: dict[str, Any] = {
            "display_name": display_name,
            "bot_token": bot_token,
            "webhook_url": webhook_url,
            "inbound": inbound,
            **kwargs,
        }

        conn = ChannelConnection(
            channel=channel,
            via=via_enum,
            status=(
                ConnectionStatus.ACTIVE
                if via_enum == Via.SELF_HOST
                else ConnectionStatus.REQUESTED
            ),
            config=config,
        )
        self._connections.append(conn)
        return conn

    def list(self) -> list[ChannelConnection]:
        """List all connections."""
        return list(self._connections)
