"""Slack Socket Mode protocol — envelope ack, disconnect-as-reconnect. No I/O."""

from __future__ import annotations

import json
from typing import Any

from caspian.adapters.plan import http_json
from caspian.adapters.socket import SocketDecision, SocketUrl
from caspian.connection import Connection
from caspian.core.errors import ProvisionError
from caspian.core.ports import RawInbound, Result, Sent

_API_BASE = "https://slack.com/api"


class SlackSocket:
    """Frame machine for one Socket Mode app. Session holds the WebSocket."""

    def __init__(self, app_token: str, *, api_base: str = _API_BASE) -> None:
        self._app_token = app_token
        self._api_base = api_base.rstrip("/")

    def open_plan(self) -> Result:
        return Result.ok(
            http_json(
                url=f"{self._api_base}/apps.connections.open",
                native="apps.connections.open",
                method="POST",
                headers={"Authorization": f"Bearer {self._app_token}"},
            )
        )

    def url_of(self, sent: Sent) -> SocketUrl:
        data = sent.raw.get("response") or {}
        if not data.get("ok"):
            return SocketUrl(fatal=str(data.get("error", "apps.connections.open failed")))
        return SocketUrl(url=str(data.get("url", "")))

    def on_frame(self, frame: dict[str, Any]) -> SocketDecision:
        kind = frame.get("type")
        if kind == "hello":
            return SocketDecision()
        if kind == "disconnect":
            return SocketDecision(reconnect=True)
        envelope_id = frame.get("envelope_id")
        send = (json.dumps({"envelope_id": envelope_id}),) if envelope_id else ()
        if kind != "events_api":
            return SocketDecision(send=send)
        payload = frame.get("payload") or {}
        return SocketDecision(
            send=send,
            sink=RawInbound(body=json.dumps(payload).encode()),
        )

    def heartbeat_payload(self) -> str | None:
        return None

    def connect_kwargs(self) -> dict[str, Any]:
        return {"ping_interval": None}


def socket_driver(conn: Connection) -> Result:
    app_token = conn.config.get("app_token", "")
    if not app_token:
        return Result.err(
            ProvisionError(
                reason=(
                    "slack socket mode needs an app_token (xapp-, scope "
                    "connections:write) alongside the bot_token; without a "
                    "public URL there is no webhook to fall back to"
                )
            )
        )
    return Result.ok(SlackSocket(app_token))
