"""Shared 'Caspian' Discord bot: one-click OAuth install + guild-based routing.

One bot in many servers; each developer's server (guild) maps to their own
connection, and messages route by guild_id to that developer's agent.
"""

from urllib.parse import parse_qs, urlparse

from comm_gateway.config import Settings
from comm_gateway.jobs import ingest_inbound, run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.providers.discord import DiscordProvider, parse_gateway_message
from fastapi.testclient import TestClient

API_KEY = "comm_shared_disc"


def _app():
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False,
        public_base_url="https://gw.test",
        discord_client_id="CID", discord_bot_token="SHARED_TOKEN",
    )
    provider = DiscordProvider(shared_bot_token="SHARED_TOKEN")
    return create_app(settings, providers={"discord": provider})


def _client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def _frame(guild_id, text="hi", mid="m1"):
    d = {"id": mid, "channel_id": "chan", "content": text,
         "author": {"id": "7", "username": "cust"}}
    if guild_id is not None:
        d["guild_id"] = guild_id
    return {"t": "MESSAGE_CREATE", "d": d}


def test_parse_routes_by_guild_for_shared_bot():
    msgs = parse_gateway_message(_frame("G1"), "shared", route_by_guild=True)
    assert len(msgs) == 1 and msgs[0].provider_inbox_id == "G1"
    # shared bot: a DM (no guild) has nothing to route by -> dropped
    assert parse_gateway_message(_frame(None), "shared", route_by_guild=True) == []
    # BYO bot still routes by application id
    assert parse_gateway_message(_frame("G1"), "APP")[0].provider_inbox_id == "APP"


def test_send_uses_shared_token_when_connection_has_none():
    p = DiscordProvider(shared_bot_token="SHARED")
    assert p._token({"guild_id": "G"}) == "SHARED"   # no bot_token -> shared
    assert p._token({"bot_token": "OWN"}) == "OWN"    # BYO token wins


def test_install_then_callback_then_inbound_routes_to_the_agent():
    app = _app()
    c = _client(app)

    # install -> pending connection + a Discord add-to-server authorize URL
    r = c.post("/v1/connections/discord/install", json={})
    assert r.status_code == 201
    conn = r.json()
    assert conn["status"] == "pending_oauth"
    assert "oauth2/authorize" in conn["authorize_url"]
    state = parse_qs(urlparse(conn["authorize_url"]).query)["state"][0]

    # Discord redirects back with the guild the bot was added to -> connection active
    cb = c.get("/v1/oauth/discord/callback", params={"state": state, "guild_id": "GUILD9"})
    assert cb.status_code == 200
    active = c.get(f"/v1/connections/{conn['id']}").json()
    assert active["status"] == "active"
    assert active["address"] == "discord://guild/GUILD9"

    # a message in that guild routes to THIS connection
    inbound = parse_gateway_message(_frame("GUILD9", text="hello agent"), "shared",
                                    route_by_guild=True)
    ingest_inbound(app.state.session_factory, "discord", inbound)
    run_pending_jobs(app.state.session_factory, app.state.providers)
    events = c.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hello agent"
    assert events[-1]["data"]["connection_id"] == conn["id"]


def test_install_with_custom_name_sets_nickname(monkeypatch):
    app = _app()
    c = _client(app)
    # capture the nickname PATCH instead of hitting Discord
    calls = {}

    def fake_set_nick(base_url, bot_token, guild_id, nick):
        calls["guild"] = guild_id
        calls["nick"] = nick

    import comm_gateway.routes.oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "set_bot_nickname", fake_set_nick)

    r = c.post("/v1/connections/discord/install",
               json={"display_name": "Acme Support"}).json()
    state = parse_qs(urlparse(r["authorize_url"]).query)["state"][0]
    cb = c.get("/v1/oauth/discord/callback", params={"state": state, "guild_id": "G77"})
    assert cb.status_code == 200
    # the developer's custom name was applied as the bot's per-server nickname
    assert calls == {"guild": "G77", "nick": "Acme Support"}


def test_install_permissions_include_change_nickname():
    app = _app()
    c = _client(app)
    r = c.post("/v1/connections/discord/install", json={}).json()
    perms = parse_qs(urlparse(r["authorize_url"]).query)["permissions"][0]
    # 67177472 = view + send + read history + CHANGE_NICKNAME
    assert perms == "67177472"


def test_install_requires_shared_bot_config():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False)
    app = create_app(settings, providers={"discord": DiscordProvider()})
    c = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = c.post("/v1/connections/discord/install", json={})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]


def test_second_server_on_same_guild_is_rejected():
    app = _app()
    c = _client(app)
    r1 = c.post("/v1/connections/discord/install", json={}).json()
    s1 = parse_qs(urlparse(r1["authorize_url"]).query)["state"][0]
    c.get("/v1/oauth/discord/callback", params={"state": s1, "guild_id": "DUP"})
    r2 = c.post("/v1/connections/discord/install", json={}).json()
    s2 = parse_qs(urlparse(r2["authorize_url"]).query)["state"][0]
    cb = c.get("/v1/oauth/discord/callback", params={"state": s2, "guild_id": "DUP"})
    assert cb.status_code == 409
