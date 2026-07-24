"""Bluesky adapter using the official AT Protocol XRPC APIs.

Each connected account supplies its own Bluesky identifier and app password.
The adapter authenticates through ``com.atproto.server.createSession``, creates
posts and replies through ``com.atproto.repo.createRecord``, and receives
mentions and replies by polling ``app.bsky.notification.listNotifications``.

Inbound notifications are normalized into Caspian ``InboundMessage`` objects.
Provider message identifiers contain the AT URI/CID references required for
future replies, but those Bluesky-specific details never leave this package as
public types.

Only opt-in interactions are received: mentions and replies. Likes, follows,
reposts, and other notification reasons are ignored.
"""

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx

from .base import (
    Capability,
    ProvisionRequest,
    ProvisionResult,
)

SESSION_PATH = "/xrpc/com.atproto.server.createSession"
CREATE_RECORD_PATH = "/xrpc/com.atproto.repo.createRecord"
LIST_NOTIFICATIONS_PATH = "/xrpc/app.bsky.notification.listNotifications"
POST_COLLECTION = "app.bsky.feed.post"
TOKEN_HEADER = "x-caspian-webhook-token"
INVALID_MESSAGE_ID_ERROR = "invalid Bluesky provider_message_id"


def _utc_now() -> str:
    """Return an AT Protocol-compatible UTC timestamp."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _encode_message_id(
    *,
    uri: str,
    cid: str,
    root_uri: str | None = None,
    root_cid: str | None = None,
) -> str:
    """Encode the strong references needed to reply without another lookup."""
    value = {
        "uri": uri,
        "cid": cid,
        "root_uri": root_uri or uri,
        "root_cid": root_cid or cid,
    }
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"bsky:{encoded}"


def _decode_message_id(provider_message_id: str) -> dict[str, str]:
    """Decode a Bluesky provider message identifier."""
    prefix = "bsky:"

    if not provider_message_id.startswith(prefix):
        raise ValueError(INVALID_MESSAGE_ID_ERROR)

    encoded = provider_message_id[len(prefix) :]
    padded = encoded + "=" * (-len(encoded) % 4)

    try:
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
    except ValueError as exc:
        raise ValueError(INVALID_MESSAGE_ID_ERROR) from exc

    required = {"uri", "cid", "root_uri", "root_cid"}

    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not all(
            isinstance(value[key], str) and value[key]
            for key in required
        )
    ):
        raise ValueError(INVALID_MESSAGE_ID_ERROR)

    return value


class BlueskyProvider:
    """Caspian channel provider for Bluesky accounts."""

    name = "bluesky"
    channel = "bluesky"

    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    connect_credentials: tuple[str, ...] = (
        "identifier",
        "app_password",
    )
    optional_connect_credentials: tuple[str, ...] = ()

    def __init__(
        self,
        base_url: str = "https://bsky.social",
        webhook_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=30.0,
        )

    def _create_session(
        self,
        credentials: Mapping[str, str] | None,
    ) -> dict:
        """Authenticate a connected account and return its session response."""
        creds = credentials or {}

        identifier = creds.get("identifier")
        app_password = creds.get("app_password")

        if not identifier or not app_password:
            raise ValueError(
                "bluesky requires identifier and app_password credentials"
            )

        response = self._client.post(
            SESSION_PATH,
            json={
                "identifier": identifier,
                "password": app_password,
            },
        )
        response.raise_for_status()

        session = response.json()
        if not isinstance(session, dict):
            raise ValueError("bluesky createSession returned an invalid response")

        return session

    def provision(
        self,
        request: ProvisionRequest,
    ) -> ProvisionResult:
        """Validate credentials and provision the connected Bluesky account."""
        session = self._create_session(request.credentials)

        did = session.get("did")
        handle = session.get("handle")

        if not isinstance(did, str) or not did:
            raise ValueError("bluesky createSession response is missing did")

        if not isinstance(handle, str) or not handle:
            raise ValueError("bluesky createSession response is missing handle")

        return ProvisionResult(
            address=handle,
            provider_resource_id=did,
        )