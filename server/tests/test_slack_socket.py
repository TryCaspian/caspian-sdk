"""Slack Socket Mode listener: the dispatch logic (ack + parse + route).

No live Slack — feeds synthetic Socket Mode frames through the client's dispatch
and asserts it acks the envelope and produces correctly-routed inbound messages.
"""

import asyncio
import json

import pytest
from comm_gateway.listeners.slack_socket import SlackSocketClient


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(json.loads(data))


def _events_frame(text="hi"):
    return {
        "type": "events_api",
        "envelope_id": "env-1",
        "payload": {
            "api_app_id": "A123",
            "team_id": "T123",
            "event_id": "Ev-1",
            "event": {
                "type": "message",
                "channel": "C1",
                "ts": "1700000000.1",
                "user": "U1",
                "text": text,
                "channel_type": "im",
            },
        },
    }


def test_socket_acks_and_routes_inbound():
    got = []
    client = SlackSocketClient("xapp-1-A123-secret", "conn_1", got.extend)
    ws = _FakeWS()
    asyncio.run(client._dispatch(ws, _events_frame("hello there")))

    # Envelope acked immediately (Slack redelivers unacked ones).
    assert ws.sent == [{"envelope_id": "env-1"}]
    # Parsed + routed: provider_inbox_id must be api_app_id:team_id so ingest
    # matches it to the connection's provider_resource_id.
    assert len(got) == 1
    assert got[0].text == "hello there"
    assert got[0].provider_inbox_id == "A123:T123"


def test_socket_hello_is_noop_and_disconnect_reconnects():
    client = SlackSocketClient("xapp-1-A123-secret", "conn_1", lambda msgs: None)
    ws = _FakeWS()

    asyncio.run(client._dispatch(ws, {"type": "hello"}))
    assert ws.sent == []  # nothing to ack, nothing sent

    with pytest.raises(ConnectionError):
        asyncio.run(client._dispatch(ws, {"type": "disconnect"}))


def test_bot_messages_ignored_but_still_acked():
    got = []
    client = SlackSocketClient("xapp-1-A123-secret", "conn_1", got.extend)
    ws = _FakeWS()
    frame = _events_frame()
    frame["payload"]["event"]["bot_id"] = "B1"  # our own / another bot -> ignore
    asyncio.run(client._dispatch(ws, frame))

    assert ws.sent == [{"envelope_id": "env-1"}]  # acked so Slack stops retrying
    assert got == []  # but not routed to the agent
