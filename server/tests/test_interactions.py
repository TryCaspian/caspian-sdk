"""Button/action round-trip: a tap on a callback button reaches the agent as an
interaction.received event (distinct from message.received) carrying the decoded
value and the message the button was attached to."""

from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.providers.discord import parse_gateway_interaction
from comm_gateway.providers.fakes.fake_social import FakeSlackProvider
from comm_gateway.providers.slack import parse_block_actions
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _telegram_provider(app):
    return app.state.providers["fake-telegram"]


def _provision_telegram(client, run_jobs) -> dict:
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support"}).json()
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    ).json()
    run_jobs()
    return client.get(f"/v1/connections/{conn['id']}").json()


# --- Telegram: callback_query -> interaction.received (end to end) ----------- #

def test_telegram_callback_query_becomes_interaction(app, client, run_jobs):
    connection = _provision_telegram(client, run_jobs)
    provider = _telegram_provider(app)

    # An inbound message stands in as the message the button was attached to, so
    # the interaction's source_message resolves to a stored row.
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=4242, text="menu", message_id=7),
    )
    run_jobs()
    inbound = client.get("/v1/events", params={"type": "message.received"}).json()[-1]
    source_id = inbound["data"]["message"]["id"]

    # The button tap.
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.callback_payload(chat_id=4242, message_id=7, value="reorder_123"),
    )
    run_jobs()

    interactions = client.get("/v1/events", params={"type": "interaction.received"}).json()
    assert len(interactions) == 1
    data = interactions[0]["data"]
    assert data["value"] == "reorder_123"
    assert data["connection_id"] == connection["id"]
    assert data["conversation_id"] is not None
    assert data["source_message"]["id"] == source_id
    # a callback tap is NOT a message
    assert not client.get("/v1/events", params={"type": "message.received"}).json()[-1][
        "data"
    ]["message"]["text"].startswith("caspian")


def test_duplicate_callback_is_deduped(app, client, run_jobs):
    _provision_telegram(client, run_jobs)
    provider = _telegram_provider(app)
    payload = provider.callback_payload(chat_id=99, message_id=3, value="x")
    for _ in range(2):
        client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    run_jobs()
    events = client.get("/v1/events", params={"type": "interaction.received"}).json()
    assert len(events) == 1


# --- Slack: block_actions -> interaction (parse + route) --------------------- #

def test_slack_block_actions_parse_decodes_value_and_source():
    provider = FakeSlackProvider()
    body = provider.block_actions_payload(channel="C1", ts="1.5", value="approve_42")
    from comm_gateway.providers.slack import _interaction_payload

    msgs = parse_block_actions(_interaction_payload(body))
    assert len(msgs) == 1
    inter = msgs[0]
    assert inter.kind == "interaction"
    assert inter.action["value"] == "approve_42"
    assert inter.action["source_message_id"] == "C1:1.5"
    assert inter.provider_inbox_id == f"{provider.app_id}:{provider.team_id}"


def _slack_active(app, provider, api_key):
    sc = TestClient(app, headers={"Authorization": f"Bearer {api_key}"})
    conn = sc.post("/v1/connections/slack/install", json={}).json()
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection

        state = session.get(Connection, conn["id"]).provider_credentials["oauth_state"]
    anon = TestClient(app)
    anon.get(f"/v1/oauth/{provider.name}/callback",
             params={"code": "abc", "state": state}, follow_redirects=True)
    run_pending_jobs(app.state.session_factory, app.state.providers)
    return sc, conn


def test_slack_interaction_route_emits_interaction_event():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    provider = FakeSlackProvider()
    app = create_app(settings, providers={provider.name: provider})
    sc, conn = _slack_active(app, provider, API_KEY)
    anon = TestClient(app)

    body = provider.block_actions_payload(channel="C1", ts="1.5", value="approve_42")
    r = anon.post(f"/internal/providers/{provider.name}/interactions", content=body,
                  headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    run_pending_jobs(app.state.session_factory, app.state.providers)

    events = sc.get("/v1/events", params={"type": "interaction.received"}).json()
    assert len(events) == 1
    assert events[0]["data"]["value"] == "approve_42"
    assert events[0]["data"]["connection_id"] == conn["id"]


# --- Discord: INTERACTION_CREATE parse (unit; gateway wiring needs live test) - #

def test_discord_interaction_parse_decodes_custom_id():
    frame = {
        "t": "INTERACTION_CREATE",
        "d": {
            "id": "int1",
            "type": 3,
            "data": {"custom_id": "caspian:reorder_9", "component_type": 2},
            "message": {"id": "M1", "channel_id": "C1"},
            "member": {"user": {"id": "U1", "username": "cust"}},
            "guild_id": "G1",
            "channel_id": "C1",
        },
    }
    msgs = parse_gateway_interaction(frame, application_id="APP")
    assert len(msgs) == 1
    inter = msgs[0]
    assert inter.kind == "interaction"
    assert inter.action == {"value": "reorder_9", "source_message_id": "C1:M1"}
    assert inter.provider_inbox_id == "APP"
    assert inter.external_event_id == "int1"
