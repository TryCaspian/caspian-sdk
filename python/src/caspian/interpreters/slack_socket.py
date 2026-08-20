"""Slack Socket Mode runner — self-host Slack with no public URL.

The webhook path needs a public HTTPS route and a signing secret. Socket Mode
needs neither: POST apps.connections.open with an app-level token (xapp-) to get
a short-lived wss URL, hold it, and Slack pushes events down it. Nothing changes
on the Slack side and no OAuth is involved.

Two rules the protocol enforces and this respects:

  * Ack every envelope IMMEDIATELY, before running the handler. Slack redelivers
    anything unacked after about 3 seconds, so acking after a slow handler means
    the same message is processed several times.
  * A "disconnect" frame is routine, not an error. Slack cycles sockets to
    rebalance, so it is treated as a normal reconnect.

A bad app token is fatal and stops the loop; there is no point spinning on
invalid_auth. Everything else reconnects with backoff.

The sink is (RawInbound) -> list[Result], so ProcessInterpreter.handle_webhook
drops straight in and the payload is the same Events API envelope the webhook
route would have received.

asyncio/websockets live here (interpreters/), never in core. `websockets` is an
optional dependency: install with `pip install "caspian[slack-socket]"`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from caspian.core.ports import RawInbound, Result

Sink = Callable[[RawInbound], list[Result]]

log = logging.getLogger("caspian.slack")

SLACK_API = "https://slack.com/api"


class SlackAuthError(Exception):
    """Fatal: the app-level token is bad. Stop rather than reconnect-spin."""


class SlackSocketRunner:
    """Holds one Socket Mode connection and feeds inbound to a sink."""

    def __init__(
        self,
        app_token: str,
        sink: Sink,
        *,
        api_base: str = SLACK_API,
        connect: Any = None,
        open_url: Any = None,
    ) -> None:
        self._app_token = app_token
        self._sink = sink
        self._api_base = api_base.rstrip("/")
        # Injectable so tests drive the protocol without a network.
        self._connect = connect
        self._open_url = open_url
        self._results: list[Result] = []
        self._events_seen = 0
        self._max_events: int | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def _wss_url(self) -> str:
        if self._open_url is not None:
            return await self._open_url(self._app_token)
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._api_base}/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}"},
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            # invalid_auth / not_allowed_token_type: the token is wrong, and
            # retrying cannot fix it.
            raise SlackAuthError(str(data.get("error", "apps.connections.open failed")))
        return str(data["url"])

    async def _dispatch(self, ws: Any, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "hello":
            log.info("socket mode connected")
            return
        if kind == "disconnect":
            # Routine: Slack cycles sockets. Reconnect rather than treating it
            # as a failure.
            raise ConnectionError("slack asked us to reconnect")

        # Ack BEFORE the handler runs. Slack redelivers unacked envelopes after
        # roughly 3 seconds, and handlers here call an LLM.
        envelope_id = frame.get("envelope_id")
        if envelope_id:
            await ws.send(json.dumps({"envelope_id": envelope_id}))

        if kind != "events_api":
            return
        payload = frame.get("payload") or {}
        self._events_seen += 1
        raw = RawInbound(body=json.dumps(payload).encode())
        # Off the event loop: the handler blocks, and blocking here would stall
        # the socket and stop us acking anything else.
        self._results.extend(await asyncio.to_thread(self._sink, raw))

    async def _run_once(self) -> None:
        url = await self._wss_url()
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        # ping_interval=None: Slack drives keepalive and websockets auto-pongs.
        async with connect(url, ping_interval=None) as ws:
            while not self._stop:
                try:
                    frame = json.loads(await ws.recv())
                except (ValueError, TypeError):
                    continue  # a malformed frame must not kill the socket
                await self._dispatch(ws, frame)
                if self._max_events is not None and self._events_seen >= self._max_events:
                    self._stop = True
                    return

    async def run(self, *, max_events: int | None = None) -> list[Result]:
        """Hold the socket open, reconnecting forever. Returns send results."""
        self._max_events = max_events
        self._stop = False
        backoff = 1.0
        while not self._stop:
            try:
                await self._run_once()
                backoff = 1.0
            except SlackAuthError as exc:
                log.error("fatal auth error, not retrying: %s", exc)
                return self._results
            except Exception as exc:  # noqa: BLE001 - any drop is a reconnect
                if self._stop:
                    break
                log.warning("socket dropped (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        return self._results


__all__ = ["SlackAuthError", "SlackSocketRunner", "Sink"]
