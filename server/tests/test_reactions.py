"""Reactions: outbound react() hits each provider's reaction API, and an inbound
reaction parses into a reaction.received event against the message it targets."""

import json

import httpx
from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.providers.discord import DiscordProvider, parse_gateway_reaction
from comm_gateway.providers.fakes.fake_social import FakeSlackProvider
from comm_gateway.providers.slack import SlackProvider, parse_event
from comm_gateway.providers.telegram import TelegramProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


# --- outbound react() hits the provider -------------------------------------- #

def test_slack_react_calls_reactions_add():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    p = SlackProvider(client_id="c")
    p._client = httpx.Client(base_url=str(p._client.base_url),
                             transport=httpx.MockTransport(handler))
    p.react("C9:1.23", ":thumbsup:", credentials={"bot_token": "xoxb"})
    assert seen["path"].endswith("/reactions.add")
    assert seen["body"] == {"channel": "C9", "timestamp": "1.23", "name": "thumbsup"}


def test_telegram_react_calls_set_message_reaction():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    p = TelegramProvider()
    p._client = httpx.Client(base_url=str(p._client.base_url),
                             transport=httpx.MockTransport(handler))
    p.react("777:42", "👍", credentials={"bot_token": "111:AAA"})
    assert seen["path"].endswith("/setMessageReaction")
    assert seen["body"]["chat_id"] == "777"
    assert seen["body"]["message_id"] == 42
    assert seen["body"]["reaction"] == [{"type": "emoji", "emoji": "👍"}]


def test_discord_react_puts_reaction():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(204)

    p = DiscordProvider()
    p._client = httpx.Client(base_url=str(p._client.base_url),
                             transport=httpx.MockTransport(handler))
    p.react("C1:M1", "👍", credentials={"bot_token": "bot"})
    assert seen["method"] == "PUT"
    assert "/channels/C1/messages/M1/reactions/" in seen["path"]
    assert seen["path"].endswith("/@me")


# --- inbound reaction -> reaction.received ----------------------------------- #

def test_slack_reaction_event_parses():
    msgs = parse_event({
        "api_app_id": "A1", "team_id": "TW", "event_id": "e1",
        "event": {"type": "reaction_added", "user": "U9", "reaction": "tada",
                  "item": {"type": "message", "channel": "C1", "ts": "1.5"},
                  "event_ts": "2.0"},
    })
    assert len(msgs) == 1
    r = msgs[0]
    assert r.kind == "reaction"
    assert r.reaction == {"emoji": "tada", "action": "added", "source_message_id": "C1:1.5"}
    assert r.provider_inbox_id == "A1:TW"


def test_discord_reaction_event_parses():
    frame = {"t": "MESSAGE_REACTION_ADD",
             "d": {"user_id": "U1", "channel_id": "C1", "message_id": "M1",
                   "guild_id": "G1", "emoji": {"name": "👍", "id": None}}}
    msgs = parse_gateway_reaction(frame, application_id="APP")
    assert msgs[0].kind == "reaction"
    assert msgs[0].reaction["emoji"] == "👍"
    assert msgs[0].reaction["source_message_id"] == "C1:M1"


def _slack_active(app, provider):
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = sc.post("/v1/connections/slack/install", json={}).json()
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection

        state = session.get(Connection, conn["id"]).provider_credentials["oauth_state"]
    TestClient(app).get(f"/v1/oauth/{provider.name}/callback",
                        params={"code": "abc", "state": state}, follow_redirects=True)
    run_pending_jobs(app.state.session_factory, app.state.providers)
    return sc, conn


def test_slack_inbound_reaction_emits_event_end_to_end():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    provider = FakeSlackProvider()
    app = create_app(settings, providers={provider.name: provider})
    sc, conn = _slack_active(app, provider)
    anon = TestClient(app)

    payload = provider.reaction_event(channel="C1", ts="1.5", emoji="thumbsup")
    anon.post(f"/internal/providers/{provider.name}/webhooks", json=payload)
    run_pending_jobs(app.state.session_factory, app.state.providers)

    events = sc.get("/v1/events", params={"type": "reaction.received"}).json()
    assert len(events) == 1
    assert events[0]["data"]["emoji"] == "thumbsup"
    assert events[0]["data"]["action"] == "added"
    assert events[0]["data"]["connection_id"] == conn["id"]


# --- react() API endpoint ---------------------------------------------------- #

def test_react_endpoint_requires_capability_and_hits_provider(app, client, run_jobs):
    # Provision a telegram connection, receive a message, then react to it.
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "S"}).json()
    client.post("/v1/connections/telegram",
                json={"customer_id": customer["id"], "agent_id": agent["id"]}).json()
    run_jobs()
    provider = app.state.providers["fake-telegram"]
    client.post("/internal/providers/fake-telegram/webhooks",
                json=provider.webhook_payload(chat_id=4242, text="hi", message_id=7))
    run_jobs()
    msg_id = client.get("/v1/events", params={"type": "message.received"}).json()[-1][
        "data"]["message"]["id"]

    r = client.post(f"/v1/messages/{msg_id}/react", json={"emoji": "👍"})
    assert r.status_code == 202
    assert r.json()["ok"] is True
