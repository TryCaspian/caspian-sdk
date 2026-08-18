"""Discord Gateway runner — the WebSocket inbound path for self-host Discord.

Discord delivers ordinary channel and DM messages over a persistent WebSocket,
never over an HTTP webhook (its webhook events cover app lifecycle and Social
SDK lobbies only). Everything else in this SDK is webhook- or poll-shaped, so
this is the one inbound transport that holds a connection open.

It speaks the minimum of the Gateway protocol: IDENTIFY with the message
intents, heartbeat on the interval the server names, RESUME after a drop, and
hand each MESSAGE_CREATE to a sink. The sink is (RawInbound) -> list[Result],
so ProcessInterpreter.handle_webhook is a drop-in fit and the parsing, rule
matching and sending stay exactly the same as every other channel.

Only the frame's inner "d" object is passed on, because that is the shape the
Discord adapter's parse() expects.

asyncio/websockets live here (interpreters/), never in core. `websockets` is an
optional dependency: install with `pip install "caspian[discord]"`.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from typing import Any

from caspian.core.ports import RawInbound, Result

Sink = Callable[[RawInbound], list[Result]]

# GUILD_MESSAGES (1<<9) | GUILD_MESSAGE_REACTIONS (1<<10) | DIRECT_MESSAGES
# (1<<12) | DIRECT_MESSAGE_REACTIONS (1<<13) | MESSAGE_CONTENT (1<<15).
# MESSAGE_CONTENT is privileged: toggle it on in the dev portal, which is
# self-serve below 10,000 users. DMs carry content without it.
INTENTS = (1 << 9) | (1 << 10) | (1 << 12) | (1 << 13) | (1 << 15)

_OP_DISPATCH, _OP_HEARTBEAT, _OP_IDENTIFY = 0, 1, 2
_OP_RESUME, _OP_RECONNECT, _OP_INVALID_SESSION = 6, 7, 9
_OP_HELLO, _OP_HEARTBEAT_ACK = 10, 11

_FORWARDED = ("MESSAGE_CREATE", "MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE")

_API_BASE = "https://discord.com/api/v10"


class DiscordGatewayRunner:
    """Holds one bot's Gateway connection and feeds inbound to a sink.

    Reconnects with exponential backoff forever; one bad frame never kills the
    loop. Call stop() to end it, or bound it with max_events in tests.
    """

    def __init__(
        self,
        bot_token: str,
        sink: Sink,
        *,
        api_base: str = _API_BASE,
        intents: int = INTENTS,
        connect: Any = None,
        http_get: Any = None,
    ) -> None:
        self._token = bot_token
        self._sink = sink
        self._api_base = api_base.rstrip("/")
        self._intents = intents
        # Injectable so tests drive the protocol without a network or a server.
        self._connect = connect
        self._http_get = http_get
        self._seq: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None
        self._results: list[Result] = []
        self._events_seen = 0
        self._max_events: int | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ─── protocol ────────────────────────────────────────────────────────────

    async def _gateway_url(self) -> str:
        if self._resume_url:
            return self._resume_url
        if self._http_get is not None:
            return await self._http_get(f"{self._api_base}/gateway/bot", self._token)
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{self._api_base}/gateway/bot",
                headers={"Authorization": f"Bot {self._token}"},
            )
            response.raise_for_status()
            return str(response.json()["url"])

    def _identify(self) -> str:
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

    def _resume(self) -> str:
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

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        await asyncio.sleep(interval * random.random())  # jitter, per Discord
        while True:
            await ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": self._seq}))
            await asyncio.sleep(interval)

    async def _read_loop(self, ws: Any) -> None:
        while not self._stop:
            frame = json.loads(await ws.recv())
            if frame.get("s") is not None:
                self._seq = frame["s"]
            op = frame.get("op")
            if op == _OP_DISPATCH:
                self._dispatch(frame)
                if self._max_events is not None and self._events_seen >= self._max_events:
                    self._stop = True
                    return
            elif op == _OP_RECONNECT:
                return  # reconnect and RESUME
            elif op == _OP_INVALID_SESSION:
                self._session_id = None
                self._resume_url = None
                return

    def _dispatch(self, frame: dict[str, Any]) -> None:
        name = frame.get("t")
        data = frame.get("d") or {}
        if name == "READY":
            self._session_id = data.get("session_id")
            self._resume_url = data.get("resume_gateway_url")
            return
        if name not in _FORWARDED:
            return
        self._events_seen += 1
        # The adapter parses the inner payload, not the gateway envelope.
        self._results.extend(self._sink(RawInbound(body=json.dumps(data).encode())))

    async def _run_once(self) -> None:
        url = await self._gateway_url()
        if "?" not in url:
            url = f"{url}?v=10&encoding=json"
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        async with connect(url, max_size=None) as ws:
            hello = json.loads(await ws.recv())
            interval = float(hello["d"]["heartbeat_interval"]) / 1000.0
            beat = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                await ws.send(self._resume() if self._session_id else self._identify())
                await self._read_loop(ws)
            finally:
                beat.cancel()

    async def run(self, *, max_events: int | None = None) -> list[Result]:
        """Hold the connection open, reconnecting forever. Returns send results.

        max_events bounds the loop so tests (and probes) terminate; production
        leaves it None and the coroutine runs for the life of the process.
        """
        self._max_events = max_events
        self._stop = False
        backoff = 1.0
        while not self._stop:
            try:
                await self._run_once()
                backoff = 1.0
            except Exception:  # noqa: BLE001 - any drop is a reconnect, never fatal
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        return self._results


__all__ = ["INTENTS", "DiscordGatewayRunner", "Sink"]
