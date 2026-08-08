"""Slack Socket Mode listener: hold a WebSocket to Slack for a BYO-token app.

Mirrors the Discord gateway client. Open ``apps.connections.open`` with the
app-level token (``xapp-``) to get a short-lived ``wss`` URL, hold the socket,
**ack every envelope immediately** (Slack redelivers unacked ones), and feed each
Events API payload through the same ``parse_event`` -> ``ingest_inbound`` pipeline
the webhook route uses. Reconnect forever with backoff; a bad frame never kills
the loop, but a bad token stops it (no point spinning on invalid_auth).

This is what makes "bring your own Socket Mode app, nothing changes on Slack"
work: no OAuth, no public webhook, inbound arrives over this socket.
"""

import asyncio
import json
import logging

import httpx
import websockets

from ..providers.slack import parse_event, parse_slack_command

log = logging.getLogger("comm.listener")

SLACK_API = "https://slack.com/api"


class SlackAuthError(Exception):
    """Fatal: the app-level token is bad. Stop, don't reconnect-spin."""


class SlackSocketClient:
    """One Socket Mode WebSocket for one active BYO-token Slack connection."""

    def __init__(self, app_token: str, conn_id: str, on_message):
        self._app_token = app_token
        self._conn_id = conn_id
        self._on_message = on_message  # (list[InboundMessage]) -> None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _open_url(self) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{SLACK_API}/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}"},
            )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            # invalid_auth / not_allowed_token_type etc. -> fatal.
            raise SlackAuthError(data.get("error", "apps.connections.open failed"))
        return data["url"]

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except SlackAuthError as exc:
                log.error(
                    "slack socket %s: fatal auth error, not retrying: %s",
                    self._conn_id, exc,
                )
                return
            except Exception as exc:  # any disconnect/error -> reconnect
                log.warning(
                    "slack socket %s disconnected (%s); reconnecting in %.0fs",
                    self._conn_id, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _run_once(self) -> None:
        url = await self._open_url()
        # ping_interval=None: let Slack drive keepalive (it pings us; websockets
        # auto-pongs). Slack periodically sends a "disconnect" frame to refresh —
        # we treat that as a normal reconnect.
        async with websockets.connect(url, ping_interval=None) as ws:
            log.info("slack socket connected for connection %s", self._conn_id)
            while not self._stop.is_set():
                raw = await ws.recv()
                try:
                    frame = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                await self._dispatch(ws, frame)

    async def _dispatch(self, ws, frame: dict) -> None:
        ftype = frame.get("type")
        payload = frame.get("payload")
        etype = None
        if isinstance(payload, dict):
            event = payload.get("event")
            if isinstance(event, dict):
                etype = event.get("type")
        log.debug("slack socket %s frame: %s%s", self._conn_id, ftype,
                  f"/{etype}" if etype else "")
        if ftype == "hello":
            return
        if ftype == "disconnect":
            raise ConnectionError("slack requested reconnect")
        # ACK first, before any processing — Slack redelivers unacked envelopes.
        envelope_id = frame.get("envelope_id")
        if envelope_id:
            await ws.send(json.dumps({"envelope_id": envelope_id}))
        if ftype == "events_api":
            if not isinstance(payload, dict):
                log.error("received events_api frame without dict payload")
                return
            event = payload.get("event")
            if not isinstance(event, dict):
                return
            try:
                msgs = parse_event(payload)
                if msgs:
                    self._on_message(msgs)
            except Exception:
                log.exception("failed to process slack event")
        elif ftype == "slash_commands":
            try:
                if not isinstance(payload, dict):
                    raise ValueError("payload is not a dictionary")
                msgs = parse_slack_command(payload)
                if msgs:
                    self._on_message(msgs)
            except Exception:
                log.exception("failed to process slack slash command")
