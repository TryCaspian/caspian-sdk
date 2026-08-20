"""Polling runner — self-host long-poll loop for channels without webhooks.

Some self-hosted bots cannot receive webhooks (no public URL) and must instead
long-poll the platform for updates. Adapters stay pure: an adapter's poll()
builds a request-description Sent (e.g. Telegram getUpdates); fetch_updates
dispatches it via an injected Transport (a real HttpTransport in production, a
fake in tests) and parses the response into (updates, next_offset). The
PollingRunner ties it together, wrapping each raw update as RawInbound and
feeding it to a sink (typically ProcessInterpreter.handle_webhook).

time/json live here (interpreters/), never in core.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, Protocol

from caspian.core.errors import AdapterError
from caspian.core.ports import AdapterPort, Connection, RawInbound, Result, Sent

Sink = Callable[[RawInbound], list[Result]]


class _Transport(Protocol):
    """Anything that can dispatch a request-description Sent (Transport shape)."""

    def dispatch(self, sent: Sent) -> Result: ...


def fetch_updates(
    adapter: AdapterPort,
    connection: Connection,
    offset: int,
    transport: _Transport,
) -> Result:
    """Fetch one batch of raw updates via long-poll.

    Asks the adapter for a poll request-description, dispatches it over the
    given transport, and parses the response. Returns Result whose value is a
    tuple (list[update_dict], next_offset). Any failure is returned as a
    Result.err — never raised across the boundary.
    """
    poll = getattr(adapter, "poll", None)
    if not callable(poll):
        return Result.err(
            AdapterError(reason=f"Adapter {getattr(adapter, 'name', '?')!r} cannot poll")
        )

    request: Result = poll(offset, connection)
    if not request.is_ok:
        return request

    dispatched = transport.dispatch(request.value)
    if not dispatched.is_ok:
        return dispatched

    return _parse_batch(dispatched.value, offset)


def _parse_batch(sent: Sent, offset: int) -> Result:
    try:
        updates = _extract_updates(sent)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        return Result.err(
            AdapterError(reason=f"Invalid poll response: {e}", command_tag="getUpdates")
        )

    next_offset = offset
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int) and update_id >= next_offset:
            next_offset = update_id + 1

    return Result.ok((updates, next_offset))


def _extract_updates(sent: Sent) -> list[dict[str, Any]]:
    """Pull the update list out of a dispatched poll response.

    A body-returning transport surfaces the platform response in sent.raw under
    "body" (bytes/str) or "response" (dict); tolerate either. Telegram shape:
    {"ok": true, "result": [ {update_id, ...}, ... ]}.
    """
    raw = sent.raw if isinstance(sent, Sent) else {}
    payload: Any = raw.get("body", raw.get("response", raw))
    if isinstance(payload, bytes | bytearray | str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return []
    result = payload.get("result", [])
    if not isinstance(result, list):
        return []
    return [u for u in result if isinstance(u, dict)]


class PollingRunner:
    """Drives a long-poll loop, feeding each update to a sink.

    The sink is a callable (RawInbound) -> list[Result], for which
    ProcessInterpreter.handle_webhook is a drop-in fit. All I/O is delegated to
    the injected transport; _sleep is injectable so tests pass a no-op.
    """

    def __init__(
        self,
        adapter: AdapterPort,
        connection: Connection,
        sink: Sink,
        *,
        transport: _Transport,
        offset: int = 0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._connection = connection
        self._sink = sink
        self._transport = transport
        self._offset = offset
        self._sleep = sleep
        self._stop = False

    def poll_once(self) -> list[Result]:
        """Fetch one batch and feed each update to the sink."""
        if not callable(getattr(self._adapter, "poll", None)):
            return [
                Result.err(
                    AdapterError(
                        reason=f"Adapter {getattr(self._adapter, 'name', '?')!r} cannot poll"
                    )
                )
            ]

        fetched = fetch_updates(
            self._adapter, self._connection, self._offset, self._transport
        )
        if not fetched.is_ok:
            return [fetched]

        updates, next_offset = fetched.value
        self._offset = next_offset

        results: list[Result] = []
        for update in updates:
            raw = RawInbound(body=json.dumps(update).encode())
            results.extend(self._sink(raw))
        return results

    def stop(self) -> None:
        """Request the run_forever loop to halt after the current iteration."""
        self._stop = True

    def run_forever(
        self, *, max_iterations: int | None = None, sleep: float = 0.0
    ) -> list[Result]:
        """Loop poll_once until stopped. max_iterations bounds it for tests."""
        self._stop = False
        collected: list[Result] = []
        iterations = 0
        while not self._stop:
            collected.extend(self.poll_once())
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            self._sleep(sleep)
        return collected


__all__ = ["PollingRunner", "Sink", "fetch_updates"]
