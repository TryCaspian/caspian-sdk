"""HostedProvisioning — wraps the gateway's /v1 provisioning surface.

Hosted is the default: the gateway owns identity, so no bot token is required
here. Every method builds a pure `GatewayRequest`, sends it through the injected
`GatewayClient`, and decodes the ok response into a frozen Pydantic model. On any
error the classified `CaspianError` `Result` is propagated unchanged — nothing is
raised across this boundary. Decoding is defensive: the gateway is the source of
truth, so absent fields fall back to safe defaults rather than failing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from caspian.connection import Connection, Via
from caspian.core.ports import Result
from caspian.hosted.client import GatewayClient, GatewayRequest, GatewayResponse


class DeviceStart(BaseModel):
    """RFC 8628 device-authorization start response."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    device_code: str = ""
    user_code: str = ""
    verification_uri: str = ""
    interval: int = 5
    expires_in: int = 0


class DeviceToken(BaseModel):
    """Credentials minted once the user approves the device."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    api_key: str = ""
    project_id: str = ""


class Channels(BaseModel):
    """The list of channels the gateway can provision."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    channels: tuple[Any, ...] = Field(default_factory=tuple)


class InstallLink(BaseModel):
    """OAuth install response carrying the provider authorize URL."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    authorize_url: str = ""


class WhatsAppOnboarding(BaseModel):
    """Embedded-signup session data for WhatsApp onboarding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str = ""
    url: str = ""
    expires_in: int = 0


def _str(body: dict[str, Any], key: str, default: str = "") -> str:
    value = body.get(key, default)
    return value if isinstance(value, str) else default


def _int(body: dict[str, Any], key: str, default: int = 0) -> int:
    value = body.get(key, default)
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


class HostedProvisioning:
    """Typed wrapper over the gateway's device/connection/OAuth endpoints."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def _send(self, request: GatewayRequest) -> Result:
        return self._client.send(request)

    @staticmethod
    def _body(response: GatewayResponse) -> dict[str, Any]:
        body = response.json_body
        return body if isinstance(body, dict) else {}

    def device_start(self) -> Result:
        result = self._send(GatewayRequest(method="POST", path="/v1/auth/device/start"))
        if not result.is_ok:
            return result
        body = self._body(result.value)
        return Result.ok(
            DeviceStart(
                device_code=_str(body, "device_code"),
                user_code=_str(body, "user_code"),
                verification_uri=_str(body, "verification_uri"),
                interval=_int(body, "interval", 5),
                expires_in=_int(body, "expires_in"),
            )
        )

    def device_token(self, device_code: str) -> Result:
        result = self._send(
            GatewayRequest(
                method="POST",
                path="/v1/auth/device/token",
                json_body={"device_code": device_code},
            )
        )
        if not result.is_ok:
            return result
        body = self._body(result.value)
        return Result.ok(
            DeviceToken(
                api_key=_str(body, "api_key"),
                project_id=_str(body, "project_id"),
            )
        )

    def list_connections(self) -> Result:
        """GET /v1/connections. The body is a JSON array."""
        result = self._send(GatewayRequest(method="GET", path="/v1/connections"))
        if not result.is_ok:
            return result
        return Result.ok(result.value.json_list)

    def find_active(self, channel: str) -> str:
        """Id of an existing live connection for this channel, or ""."""
        listed = self.list_connections()
        if not listed.is_ok:
            return ""
        for row in listed.value:
            if not isinstance(row, dict):
                continue
            if row.get("channel") == channel and row.get("status") in (
                "active",
                "pending_oauth",
            ):
                return str(row.get("id", ""))
        return ""

    def add_connection(self, channel: str, options: dict[str, Any] | None = None) -> Result:
        """Ensure this project has a connection for `channel`.

        `channels.add()` is declarative ("my agent uses discord"), not a request
        to create another connection each run. Creating unconditionally means a
        restart fails once a connection exists, and channels connected by OAuth
        cannot be created this way at all: their credentials live behind the
        /install flow, so POST /v1/connections/{channel} rejects them.
        """
        existing = self.find_active(channel)
        if existing:
            return self.get_connection(existing)

        result = self._send(
            GatewayRequest(
                method="POST",
                path=f"/v1/connections/{channel}",
                json_body=options or {},
            )
        )
        return self._decode_connection(result)

    def get_connection(self, connection_id: str) -> Result:
        result = self._send(
            GatewayRequest(method="GET", path=f"/v1/connections/{connection_id}")
        )
        return self._decode_connection(result)

    def update_connection(self, connection_id: str, patch: dict[str, Any]) -> Result:
        result = self._send(
            GatewayRequest(
                method="PATCH",
                path=f"/v1/connections/{connection_id}",
                json_body=patch,
            )
        )
        return self._decode_connection(result)

    def delete_connection(self, connection_id: str) -> Result:
        result = self._send(
            GatewayRequest(method="DELETE", path=f"/v1/connections/{connection_id}")
        )
        if not result.is_ok:
            return result
        return Result.ok(result.value)

    def initiate(self, connection_id: str, conversation: str, text: str) -> Result:
        result = self._send(
            GatewayRequest(
                method="POST",
                path=f"/v1/connections/{connection_id}/initiate",
                json_body={"conversation": conversation, "text": text},
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(result.value)

    def list_channels(self) -> Result:
        result = self._send(GatewayRequest(method="GET", path="/v1/channels"))
        if not result.is_ok:
            return result
        body = self._body(result.value)
        raw = body.get("channels", [])
        items = tuple(raw) if isinstance(raw, (list, tuple)) else ()
        return Result.ok(Channels(channels=items))

    def channel_guide(self, channel: str) -> Result:
        result = self._send(
            GatewayRequest(
                method="GET",
                path=f"/v1/channels/{channel}/guide",
                text_response=True,
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(result.value.text_body)

    def install_url(self, provider: str) -> Result:
        result = self._send(
            GatewayRequest(method="POST", path=f"/v1/connections/{provider}/install")
        )
        if not result.is_ok:
            return result
        body = self._body(result.value)
        return Result.ok(InstallLink(authorize_url=_str(body, "authorize_url")))

    def whatsapp_onboarding(self) -> Result:
        result = self._send(
            GatewayRequest(
                method="POST",
                path="/v1/connections/whatsapp/onboarding-session",
            )
        )
        if not result.is_ok:
            return result
        body = self._body(result.value)
        return Result.ok(
            WhatsAppOnboarding(
                session_id=_str(body, "session_id"),
                url=_str(body, "url"),
                expires_in=_int(body, "expires_in"),
            )
        )

    def _decode_connection(self, result: Result) -> Result:
        if not result.is_ok:
            return result
        body = self._body(result.value)
        return Result.ok(
            Connection(
                id=_str(body, "id"),
                channel=_str(body, "channel"),
                via=Via.HOSTED,
                status=_str(body, "status"),
                address=_str(body, "address"),
                authorize_url=_str(body, "authorize_url"),
            )
        )
