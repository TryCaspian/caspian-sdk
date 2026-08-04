"""One Discord bot's Gateway (WebSocket) connection.

Discord delivers normal channel/DM messages over a persistent WebSocket, not an
HTTP webhook. This client speaks the minimum of the Gateway protocol to receive
them: IDENTIFY with the message intents, heartbeat on the server's interval,
RESUME after a drop, and hand each MESSAGE_CREATE to a sink. Reconnects with
backoff forever; a single bad frame never kills it.

Requires the "Message Content Intent" enabled on the bot (dev portal). Uses the
`websockets` library; all network I/O is async.
"""

import asyncio
import json
import logging
import random

import httpx
import websockets

from ..providers.discord import (
    parse_gateway_command,
    parse_gateway_interaction,
    parse_gateway_message,
    parse_gateway_reaction,
)

log = logging.getLogger("comm.listener.discord")

# GUILD_MESSAGES (1<<9) | GUILD_MESSAGE_REACTIONS (1<<10) | DIRECT_MESSAGES (1<<12)
# | DIRECT_MESSAGE_REACTIONS (1<<13) | MESSAGE_CONTENT (1<<15). Reactions need
# their own intents; button-tap INTERACTION_CREATE events are always delivered.
INTENTS = (1 << 9) | (1 << 10) | (1 << 12) | (1 << 13) | (1 << 15)
_OP_DISPATCH, _OP_HEARTBEAT, _OP_IDENTIFY = 0, 1, 2
_OP_RESUME, _OP_RECONNECT, _OP_INVALID_SESSION = 6, 7, 9
_OP_HELLO, _OP_HEARTBEAT_ACK = 10, 11


class DiscordGatewayClient:
    def __init__(self, bot_token: str, app_id: str, on_message, api_base: str,
                 route_by_guild: bool = False) -> None:
        self._token = bot_token
        self._app_id = app_id
        self._route_by_guild = route_by_guild
        self._on_message = on_message  # callable(list[InboundMessage])
        self._api_base = api_base.rstrip("/")
        self._seq: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _gateway_url(self) -> str:
        if self._resume_url:
            return self._resume_url
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self._api_base}/gateway/bot",
                            headers={"Authorization": f"Bot {self._token}"})
            r.raise_for_status()
            return r.json()["url"]

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except Exception as exc:  # any disconnect/error → reconnect
                log.warning("discord gateway %s dropped: %s; retrying in %.0fs",
                            self._app_id, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _run_once(self) -> None:
        url = await self._gateway_url() + "?v=10&encoding=json"
        async with websockets.connect(url, max_size=None) as ws:
            hello = json.loads(await ws.recv())
            interval = hello["d"]["heartbeat_interval"] / 1000.0
            hb = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                if self._session_id:
                    await ws.send(json.dumps({"op": _OP_RESUME, "d": {
                        "token": self._token, "session_id": self._session_id,
                        "seq": self._seq}}))
                else:
                    await ws.send(json.dumps({"op": _OP_IDENTIFY, "d": {
                        "token": self._token, "intents": INTENTS,
                        "properties": {"os": "linux", "browser": "comm", "device": "comm"}}}))
                await self._read_loop(ws)
            finally:
                hb.cancel()

    async def _heartbeat(self, ws, interval: float) -> None:
        # first beat jittered per Discord's guidance
        await asyncio.sleep(interval * random.random())
        while True:
            await ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": self._seq}))
            await asyncio.sleep(interval)

    async def _read_loop(self, ws) -> None:
        while not self._stop.is_set():
            frame = json.loads(await ws.recv())
            op = frame.get("op")
            if frame.get("s") is not None:
                self._seq = frame["s"]
            if op == _OP_DISPATCH:
                await self._dispatch(frame)
            elif op == _OP_RECONNECT:
                return  # reconnect + resume
            elif op == _OP_INVALID_SESSION:
                self._session_id = None
                self._resume_url = None
                return
            # _OP_HEARTBEAT_ACK / others: ignore

    async def _dispatch(self, frame: dict) -> None:
        event = frame.get("t")
        if event == "READY":
            self._session_id = frame["d"].get("session_id")
            self._resume_url = frame["d"].get("resume_gateway_url")
            log.info("discord gateway %s ready", self._app_id)
            return
        if event == "INTERACTION_CREATE":
            # Ack within 3s: type 5 for command (type 2),
            # type 6 for components (type 3) / modal (type 5)
            interaction_data = frame.get("d") or {}
            await self._ack_interaction(interaction_data)
            interaction_type = interaction_data.get("type")
            if interaction_type == 2:
                inbound = parse_gateway_command(frame, self._app_id, self._route_by_guild)
            else:
                inbound = parse_gateway_interaction(frame, self._app_id, self._route_by_guild)
        elif event in ("MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"):
            inbound = parse_gateway_reaction(frame, self._app_id, self._route_by_guild)
        elif event == "MESSAGE_CREATE":
            inbound = parse_gateway_message(frame, self._app_id, self._route_by_guild)
        else:
            return
        if inbound:
            # sink is sync (DB write); run off the event loop
            await asyncio.to_thread(self._on_message, inbound)

    async def _ack_interaction(self, data: dict) -> None:
        """Acknowledge a component interaction with a deferred update (type 6),
        or a command with type 5. Uses the per-interaction id+token, so no bot
        auth is needed. Best-effort: a failed ack must not drop the interaction
        we still parse and dispatch.
        """
        interaction_id, token = data.get("id"), data.get("token")
        if not (interaction_id and token):
            return
        interaction_type = data.get("type")
        ack_type = 5 if interaction_type == 2 else 6
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    f"{self._api_base}/interactions/{interaction_id}/{token}/callback",
                    json={"type": ack_type},
                )
        except Exception as exc:
            log.warning("discord interaction ack failed: %s", exc)
