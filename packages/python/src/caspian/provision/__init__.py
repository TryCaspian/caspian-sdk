"""Provisioning — channels.add, hosted default, self-host opt-in.

This module CANNOT import caspian.core (enforced by import-linter).
It is paperwork, not behavior. Failures are a reason string; the facade
maps them to core.errors.ProvisionError on a Result. Nothing here raises.
"""

from __future__ import annotations

from typing import Any

from caspian.catalog import CHANNELS, BotTokenWhen, needs_bot_token
from caspian.connection import Connection, ConnectionStatus, Via

__all__ = [
    "Channels",
    "Connection",
    "ConnectionStatus",
    "Via",
    "bot_token_error",
]


def bot_token_error(channel: str, via: str, bot_token: str) -> str | None:
    """Reason this add() needs a bot_token, or None if the token is present/unneeded."""
    if bot_token or not needs_bot_token(channel, via):
        return None
    row = CHANNELS.get(channel)  # type: ignore[arg-type]
    if row is not None and row.bot_token is BotTokenWhen.ALWAYS:
        return (
            f"{channel} requires bot_token (from @BotFather). "
            "Hosted does not mint a bot; it only owns inbound I/O."
        )
    return (
        f"Self-host '{channel}' requires bot_token. "
        "Omit `via` for hosted (Caspian owns the identity)."
    )


class Channels:
    """Manages channel connections. One verb: add().

    Hosted is the default. Omitting `via` never means 'I forgot a token'.
    Self-host without the required secret is an error (returned as a reason string).
    """

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._connections: list[Connection] = []

    @property
    def connections(self) -> list[Connection]:
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
    ) -> Connection | str:
        """Add a channel connection.

        Default is hosted. `via="self-host"` requires bot_token.
        Returns a Connection, or a reason string if paperwork fails.
        """
        via_enum = Via(via)
        error = bot_token_error(channel, via_enum.value, bot_token)
        if error is not None:
            return error

        config: dict[str, Any] = {
            "display_name": display_name,
            "bot_token": bot_token,
            "webhook_url": webhook_url,
            "inbound": inbound,
            **kwargs,
        }

        conn = Connection(
            id="",
            channel=channel,
            config=config,
            via=via_enum,
            status=(
                ConnectionStatus.ACTIVE
                if via_enum == Via.SELF_HOST
                else ConnectionStatus.REQUESTED
            ),
        )
        self._connections.append(conn)
        return conn

    def list(self) -> list[Connection]:
        """List all connections."""
        return list(self._connections)
