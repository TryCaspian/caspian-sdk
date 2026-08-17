"""Discord Gateway heartbeat ACK tracking.

Discord's Gateway docs: if a client does not receive a Heartbeat ACK (opcode
11) between its attempts at sending heartbeats, the connection may be
"zombied" - the client should close and reconnect. This exercises that path
against the real DiscordGatewayClient with a fake WebSocket that never sends
an ACK, and confirms the *existing* run()/backoff reconnect machinery is what
actually performs the reconnect - not a new, parallel path.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import websockets.exceptions as wexc
from comm_gateway.listeners.discord_gateway import DiscordGatewayClient

HEARTBEAT_INTERVAL_MS = 50


class FakeWebSocket:
    """Delivers HELLO once, then goes silent forever - no HEARTBEAT_ACK, no
    other frames - simulating a Gateway session that stopped responding at
    the application level while the transport stays nominally open. close()
    (called by anything, including a fixed _heartbeat()) makes any pending
    recv() raise, matching real websockets behavior."""

    def __init__(self):
        self.sent: list[dict] = []
        self._closed = asyncio.Event()
        self._hello_sent = False

    async def recv(self):
        if not self._hello_sent:
            self._hello_sent = True
            return json.dumps({"op": 10, "d": {"heartbeat_interval": HEARTBEAT_INTERVAL_MS}})
        closed = asyncio.create_task(self._closed.wait())
        silence = asyncio.create_task(asyncio.sleep(3600))
        done, pending = await asyncio.wait({closed, silence}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if closed in done:
            raise wexc.ConnectionClosedError(None, None)
        return "{}"

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self, code=1000, reason=""):
        self._closed.set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self._closed.set()


def _heartbeats_sent(ws: FakeWebSocket) -> int:
    return len([f for f in ws.sent if f.get("op") == 1])


async def _run_client_for(client: DiscordGatewayClient, seconds: float):
    connect_calls: list[FakeWebSocket] = []

    def fake_connect(url, **kwargs):
        ws = FakeWebSocket()
        connect_calls.append(ws)
        return ws

    with patch(
        "comm_gateway.listeners.discord_gateway.websockets.connect", side_effect=fake_connect
    ):
        with patch.object(client, "_gateway_url", new=AsyncMock(return_value="wss://fake")):
            task = asyncio.create_task(client.run())
            await asyncio.sleep(seconds)
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    return connect_calls


def test_missing_heartbeat_ack_triggers_reconnect_via_existing_backoff():
    """Zero HEARTBEAT_ACKs ever arrive. The client must notice and reconnect
    through run()'s existing exception+backoff path (min 1.0s), not hang
    sending heartbeats into a zombied connection forever."""
    client = DiscordGatewayClient("tok", "app1", lambda inbound: None, "https://discord.test/api")

    connects = asyncio.run(_run_client_for(client, seconds=1.6))

    assert len(connects) >= 2, (
        f"expected a reconnect after the missed ACK, got {len(connects)} connect() call(s)"
    )
    first, second = connects[0], connects[1]
    # The first connection must not keep heartbeating past the first unacked
    # beat - it should close (via the fix) rather than send a second one.
    assert _heartbeats_sent(first) == 1, (
        f"expected exactly 1 heartbeat before the missed-ack close, got {_heartbeats_sent(first)}"
    )
    # The reconnected session must resume normal heartbeating.
    assert _heartbeats_sent(second) >= 1
