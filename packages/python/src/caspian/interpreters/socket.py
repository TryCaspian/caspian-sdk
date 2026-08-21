"""Socket session — the one inbound loop for held-open connections.

Webhook, poll, and socket all end at ProcessInterpreter.handle_webhook. This
module is how bytes arrive over a socket: connect, recv, backoff. The driver
(an adapter) unwraps frames. asyncio/websockets live here, never in core.
`websockets` is optional: caspian[discord] or caspian[slack-socket].
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from typing import Any

from caspian.adapters.socket import SocketDriver
from caspian.core.ports import RawInbound, Result, TransportPort

Sink = Callable[[RawInbound], list[Result]]

log = logging.getLogger("caspian.socket")


class SocketFatal(Exception):
    """Driver says retrying cannot help (bad token). Stop the loop."""


class SocketSession:
    """Hold one socket open and feed inbound to a sink."""

    def __init__(
        self,
        driver: SocketDriver,
        sink: Sink,
        *,
        transport: TransportPort,
        connect: Any = None,
    ) -> None:
        self._driver = driver
        self._sink = sink
        self._transport = transport
        self._connect = connect
        self._results: list[Result] = []
        self._events_seen = 0
        self._max_events: int | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self, *, max_events: int | None = None) -> list[Result]:
        """Hold the connection open, reconnecting forever. Returns sink results."""
        self._max_events = max_events
        self._stop = False
        backoff = 1.0
        while not self._stop:
            try:
                await self._run_once()
                backoff = 1.0
            except SocketFatal as exc:
                log.error("fatal socket error, not retrying: %s", exc)
                return self._results
            except Exception as exc:  # noqa: BLE001 - any drop is a reconnect
                if self._stop:
                    break
                log.warning("socket dropped (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        return self._results

    async def _run_once(self) -> None:
        planned = self._driver.open_plan()
        if not planned.is_ok:
            reason = planned.error.reason if planned.error is not None else "open failed"
            raise SocketFatal(reason)
        dispatched = self._transport.dispatch(planned.value)
        if not dispatched.is_ok:
            err = dispatched.error
            reason = err.reason if err is not None else "open dispatch failed"
            raise ConnectionError(reason)
        opened = self._driver.url_of(dispatched.value)
        if opened.fatal:
            raise SocketFatal(opened.fatal)
        if not opened.url:
            raise ConnectionError("no socket url")
        connect = self._connect
        if connect is None:
            import websockets  # type: ignore[import-not-found]

            connect = websockets.connect
        heartbeat: asyncio.Task[None] | None = None
        async with connect(opened.url, **self._driver.connect_kwargs()) as ws:
            try:
                while not self._stop:
                    try:
                        raw = await ws.recv()
                    except (ValueError, TypeError):
                        continue
                    try:
                        frame = json.loads(raw)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if not isinstance(frame, dict):
                        continue
                    decision = self._driver.on_frame(frame)
                    for payload in decision.send:
                        await ws.send(payload)
                    if decision.heartbeat_interval is not None:
                        if heartbeat is not None:
                            heartbeat.cancel()
                        heartbeat = asyncio.create_task(
                            self._heartbeat(ws, decision.heartbeat_interval)
                        )
                    if decision.fatal:
                        raise SocketFatal(decision.fatal)
                    if decision.sink is not None:
                        self._events_seen += 1
                        self._results.extend(
                            await asyncio.to_thread(self._sink, decision.sink)
                        )
                        if (
                            self._max_events is not None
                            and self._events_seen >= self._max_events
                        ):
                            self._stop = True
                            return
                    if decision.reconnect:
                        return
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            payload = self._driver.heartbeat_payload()
            if payload:
                await ws.send(payload)
            await asyncio.sleep(interval)


__all__ = ["Sink", "SocketFatal", "SocketSession"]
