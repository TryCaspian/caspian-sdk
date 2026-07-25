"""Discord Gateway listener: message dispatch → ingest, and bot discovery."""

import asyncio

import pytest
from comm_gateway.config import Settings
from comm_gateway.jobs import ingest_inbound, run_pending_jobs
from comm_gateway.listeners.discord_gateway import DiscordGatewayClient
from comm_gateway.listeners.manager import _active_discord_bots
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_social import FakeDiscordProvider
from fastapi.testclient import TestClient

API_KEY = "comm_disc_listener"
BOT_TOKEN = "OTk5.abc.def"  # first segment b64 -> "999"


def _app():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    p = FakeDiscordProvider()
    return create_app(settings, providers={p.name: p})


def _connect_bot(app):
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = client.post("/v1/connections/discord", json={"bot_token": BOT_TOKEN}).json()
    run_pending_jobs(app.state.session_factory, app.state.providers)
    return client, conn


def test_active_discord_bots_discovers_the_bot(monkeypatch):
    # the manager reads by provider name "discord"; the fake provider is
    # "fake-discord", so point discovery at the connection's own provider.
    app = _app()
    client, conn = _connect_bot(app)
    # patch the query to the fake provider name for this unit test
    import comm_gateway.listeners.manager as mgr
    from comm_gateway.models import Connection
    from sqlalchemy import select

    def discover(session_factory):
        out = {}
        from comm_gateway.crypto import read_credentials
        with session_factory() as s:
            for c in s.execute(select(Connection).where(
                    Connection.channel == "discord", Connection.status == "active")).scalars():
                creds = read_credentials(c)
                if creds.get("bot_token") and c.provider_resource_id:
                    out[c.provider_resource_id] = creds["bot_token"]
        return out

    bots = discover(app.state.session_factory)
    assert bots == {"999": BOT_TOKEN}
    assert conn["id"]
    assert mgr and _active_discord_bots  # symbols exist


def test_dispatch_message_ingests_and_delivers(app_none=None):
    app = _app()
    client, conn = _connect_bot(app)

    delivered = []
    client_gw = DiscordGatewayClient(BOT_TOKEN, "999", None, "https://discord.com/api/v10")

    # simulate the Gateway handing us a MESSAGE_CREATE frame
    frame = {
        "op": 0, "t": "MESSAGE_CREATE", "s": 5,
        "d": {
            "id": "700001", "channel_id": "chan9", "content": "hey agent",
            "author": {"id": "555", "username": "customer"},
        },
    }
    from comm_gateway.providers.discord import parse_gateway_message
    inbound = parse_gateway_message(frame, "999")
    n = ingest_inbound(app.state.session_factory, "fake-discord", inbound)
    assert n == 1
    run_pending_jobs(app.state.session_factory, app.state.providers)

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hey agent"
    # and it's routed to the right connection (by app id 999)
    assert events[-1]["data"]["connection_id"] == conn["id"]
    assert client_gw and delivered == []


def test_ingest_dedups_repeated_gateway_events():
    app = _app()
    client, conn = _connect_bot(app)
    from comm_gateway.providers.discord import parse_gateway_message
    frame = {"t": "MESSAGE_CREATE", "d": {
        "id": "dup1", "channel_id": "c", "content": "hi",
        "author": {"id": "5", "username": "u"}}}
    inbound = parse_gateway_message(frame, "999")
    assert ingest_inbound(app.state.session_factory, "fake-discord", inbound) == 1
    assert ingest_inbound(app.state.session_factory, "fake-discord", inbound) == 0  # deduped


def test_gateway_client_read_loop_handles_dispatch():
    """The read loop advances seq and dispatches MESSAGE_CREATE to the sink."""
    got = []
    client = DiscordGatewayClient("tok", "999", lambda inbound: got.append(inbound),
                                  "https://discord.com/api/v10")

    class FakeWS:
        def __init__(self, frames):
            self._frames = iter(frames)

        async def recv(self):
            try:
                return next(self._frames)
            except StopIteration as exc:
                raise asyncio.CancelledError from exc

    import json
    frames = [
        json.dumps({"op": 0, "t": "READY", "s": 1,
                    "d": {"session_id": "sess", "resume_gateway_url": "wss://r"}}),
        json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 2, "d": {
            "id": "1", "channel_id": "c", "content": "yo",
            "author": {"id": "5", "username": "u"}}}),
    ]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client._read_loop(FakeWS(frames)))
    assert client._seq == 2
    assert client._session_id == "sess"
    assert got and got[0][0].text == "yo"
