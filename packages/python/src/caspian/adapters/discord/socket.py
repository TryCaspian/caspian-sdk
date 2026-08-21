"""Discord Gateway protocol — IDENTIFY, heartbeat, RESUME. No I/O."""

from __future__ import annotations

import json
from typing import Any

from caspian.adapters.plan import http_json
from caspian.adapters.socket import SocketDecision, SocketUrl
from caspian.connection import Connection
from caspian.core.errors import ProvisionError
from caspian.core.ports import RawInbound, Result, Sent

# GUILD_MESSAGES | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGES |
# DIRECT_MESSAGE_REACTIONS | MESSAGE_CONTENT.
INTENTS = (1 << 9) | (1 << 10) | (1 << 12) | (1 << 13) | (1 << 15)

_OP_DISPATCH, _OP_IDENTIFY = 0, 2
_OP_RESUME, _OP_RECONNECT, _OP_INVALID_SESSION = 6, 7, 9
_OP_HELLO = 10

_FORWARDED = ("MESSAGE_CREATE", "MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE")
_API_BASE = "https://discord.com/api/v10"


class DiscordSocket:
    """Frame machine for one bot. Session holds the WebSocket."""

    def __init__(
        self, bot_token: str, *, api_base: str = _API_BASE, intents: int = INTENTS
    ) -> None:
        self._token = bot_token
        self._api_base = api_base.rstrip("/")
        self._intents = intents
        self._seq: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None

    def open_plan(self) -> Result:
        if self._resume_url:
            return Result.ok(
                Sent(raw={"transport": "noop", "native": "resume", "url": self._resume_url})
            )
        return Result.ok(
            http_json(
                url=f"{self._api_base}/gateway/bot",
                native="gateway",
                method="GET",
                headers={"Authorization": f"Bot {self._token}"},
            )
        )

    def url_of(self, sent: Sent) -> SocketUrl:
        url = self._resume_url or str((sent.raw.get("response") or {}).get("url", ""))
        if not url:
            return SocketUrl()
        if "?" not in url:
            url = f"{url}?v=10&encoding=json"
        return SocketUrl(url=url)

    def on_frame(self, frame: dict[str, Any]) -> SocketDecision:
        if frame.get("s") is not None:
            self._seq = frame["s"]
        op = frame.get("op")
        if op == _OP_HELLO:
            interval = float((frame.get("d") or {}).get("heartbeat_interval", 45000)) / 1000.0
            return SocketDecision(heartbeat_interval=interval, send=(self._greeting(),))
        if op == _OP_RECONNECT:
            return SocketDecision(reconnect=True)
        if op == _OP_INVALID_SESSION:
            self._session_id = None
            self._resume_url = None
            return SocketDecision(reconnect=True)
        if op != _OP_DISPATCH:
            return SocketDecision()
        name = frame.get("t")
        data = frame.get("d") or {}
        if name == "READY":
            self._session_id = data.get("session_id")
            self._resume_url = data.get("resume_gateway_url")
            return SocketDecision()
        if name not in _FORWARDED:
            return SocketDecision()
        return SocketDecision(sink=RawInbound(body=json.dumps(data).encode()))

    def heartbeat_payload(self) -> str | None:
        return json.dumps({"op": 1, "d": self._seq})

    def connect_kwargs(self) -> dict[str, Any]:
        return {"max_size": None}

    def _greeting(self) -> str:
        if self._session_id:
            return json.dumps(
                {
                    "op": _OP_RESUME,
                    "d": {
                        "token": self._token,
                        "session_id": self._session_id,
                        "seq": self._seq,
                    },
                }
            )
        return json.dumps(
            {
                "op": _OP_IDENTIFY,
                "d": {
                    "token": self._token,
                    "intents": self._intents,
                    "properties": {
                        "os": "linux",
                        "browser": "caspian",
                        "device": "caspian",
                    },
                },
            }
        )


def socket_driver(conn: Connection) -> Result:
    token = conn.config.get("bot_token", "")
    if not token:
        return Result.err(ProvisionError(reason="discord self-host needs a bot_token"))
    return Result.ok(DiscordSocket(token))
