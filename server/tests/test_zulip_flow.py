import json as jsonlib

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_zulip import FakeZulipProvider
from fastapi.testclient import TestClient


def _zulip_provider(app) -> FakeZulipProvider:
    return app.state.providers["fake-zulip"]


def _provision_connection(client, run_jobs) -> dict:
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support Agent"}).json()
    response = client.post(
        "/v1/connections/zulip",
        json={
            "customer_id": customer["id"],
            "agent_id": agent["id"],
            "display_name": "Acme Zulip Bot",
        },
    )
    assert response.status_code == 201
    connection = response.json()
    assert connection["status"] == "provisioning"
    assert connection["channel"] == "zulip"
    run_jobs()
    return client.get(f"/v1/connections/{connection['id']}").json()


def test_provision_zulip_connection(client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    assert connection["status"] == "active"
    assert connection["address"]
    assert not any(k.startswith("provider") for k in connection)


def test_inbound_stream_message_to_event(app, client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    provider = _zulip_provider(app)

    payload = provider.webhook_payload(stream_id=5, topic="general", text="Where is my order?")
    response = client.post("/internal/providers/fake-zulip/webhooks", json=payload)
    assert response.status_code == 204
    run_jobs()

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1
    data = events[0]["data"]
    assert data["connection_id"] == connection["id"]
    message = data["message"]
    assert message["channel"] == "zulip"
    assert message["direction"] == "inbound"
    assert message["text"] == "Where is my order?"
    assert message["sender"]["address"] == "user@zulip.test"
    assert not any(k.startswith("provider") for k in message)


def test_inbound_dm_to_event(app, client, run_jobs):
    _provision_connection(client, run_jobs)
    provider = _zulip_provider(app)

    payload = provider.dm_payload(text="Private question")
    response = client.post("/internal/providers/fake-zulip/webhooks", json=payload)
    assert response.status_code == 204
    run_jobs()

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1
    message = events[0]["data"]["message"]
    assert message["text"] == "Private question"


def test_duplicate_delivery_is_idempotent(app, client, run_jobs):
    _provision_connection(client, run_jobs)
    provider = _zulip_provider(app)

    payload = provider.webhook_payload(text="Duplicate test")
    client.post("/internal/providers/fake-zulip/webhooks", json=payload)
    client.post("/internal/providers/fake-zulip/webhooks", json=payload)
    run_jobs()

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1


def test_messages_in_different_streams_create_separate_conversations(app, client, run_jobs):
    _provision_connection(client, run_jobs)
    provider = _zulip_provider(app)

    client.post(
        "/internal/providers/fake-zulip/webhooks",
        json=provider.webhook_payload(stream_id=5, topic="bugs", text="first"),
    )
    client.post(
        "/internal/providers/fake-zulip/webhooks",
        json=provider.webhook_payload(stream_id=5, topic="bugs", text="second"),
    )
    client.post(
        "/internal/providers/fake-zulip/webhooks",
        json=provider.webhook_payload(stream_id=9, topic="features", text="other stream"),
    )
    run_jobs()

    connection = client.get("/v1/connections").json()[0]
    conversations = client.get(
        "/v1/conversations", params={"connection_id": connection["id"]}
    ).json()
    assert len(conversations) == 2


def test_reply_flow(app, client, run_jobs):
    _provision_connection(client, run_jobs)
    provider = _zulip_provider(app)

    payload = provider.webhook_payload(stream_id=5, topic="support", text="Help me")
    client.post("/internal/providers/fake-zulip/webhooks", json=payload)
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[0]["data"]["message"]["id"]

    response = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "On it!"})
    assert response.status_code == 201
    reply = response.json()
    assert reply["status"] == "queued"
    assert reply["channel"] == "zulip"

    run_jobs()
    assert len(provider.replies) == 1
    assert provider.replies[0]["text"] == "On it!"

    sent = client.get(f"/v1/messages/{reply['id']}").json()
    assert sent["status"] == "sent"

    thread = client.get(f"/v1/conversations/{reply['conversation_id']}/messages").json()
    assert [m["direction"] for m in thread] == ["inbound", "outbound"]


def test_webhook_token_enforced():
    secured = FakeZulipProvider(webhook_token="s3cret")
    app = create_app(
        Settings(
            database_url="sqlite://",
            bootstrap_api_key="comm_test_key",
            inline_worker=False,
        ),
        providers={secured.name: secured},
    )
    secured_client = TestClient(app, headers={"Authorization": "Bearer comm_test_key"})

    payload = secured.webhook_payload(token="wrong_token")
    missing = secured_client.post("/internal/providers/fake-zulip/webhooks", json=payload)
    assert missing.status_code == 400

    payload_ok = secured.webhook_payload(token="s3cret")
    ok = secured_client.post(
        "/internal/providers/fake-zulip/webhooks",
        content=jsonlib.dumps(payload_ok),
        headers={"content-type": "application/json"},
    )
    assert ok.status_code == 204
