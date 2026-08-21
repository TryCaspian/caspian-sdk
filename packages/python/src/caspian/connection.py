"""A connected channel — one product record for adapters, add(), and hosted decode.

Provision and core both import this module (provision cannot import core).
Hosted JSON is decoded into this type at the gateway edge; there is no twin.
"""

from __future__ import annotations

from typing import Any

from caspian._compat import StrEnum


class Via(StrEnum):
    HOSTED = "hosted"
    SELF_HOST = "self-host"


class ConnectionStatus(StrEnum):
    REQUESTED = "requested"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    ERROR = "error"


class Connection:
    """A provisioned channel connection.

    ``via`` is hosted (Caspian owns inbound) or self-host (this process does).
    ``inbound_owner`` is ``gateway`` or ``local`` accordingly. ``authorize_url``
    is set when the gateway needs the user to complete OAuth.
    """

    def __init__(
        self,
        id: str,
        channel: str,
        config: dict[str, Any] | None = None,
        *,
        via: Via | str = Via.HOSTED,
        status: str = "",
        address: str = "",
        authorize_url: str = "",
    ) -> None:
        self.id = id
        self.channel = channel
        self.config = config or {}
        self.via = Via(via)
        self.status = status
        self.address = address
        self.authorize_url = authorize_url

    @property
    def inbound_owner(self) -> str:
        """``local`` if this process receives inbound; ``gateway`` if Caspian hosted does."""
        return "local" if self.via == Via.SELF_HOST else "gateway"


def overlay_remote(local: Connection, remote: Connection) -> Connection:
    """Keep local config/via; take identity fields the gateway minted."""
    return Connection(
        id=remote.id or local.id,
        channel=remote.channel or local.channel,
        config=local.config,
        via=local.via,
        status=remote.status or local.status,
        address=remote.address or local.address,
        authorize_url=remote.authorize_url or local.authorize_url,
    )
