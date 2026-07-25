from comm_gateway.providers.fakes.fake_telegram import FakeTelegramProvider
from comm_gateway.providers.telegram import parse_update
from comm_gateway.providers.telegram import SECRET_HEADER


def _telegram_provider(app) -> FakeTelegramProvider:
    return app.state.providers["fake-telegram"]


def test_telegram_bot_command_is_normalized_separately_from_messages():
    event = parse_update(
        {
            "update_id": 42,
            "message": {
                "message_id": 7,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 9, "username": "customer"},
                "text": "/help details",
            },
        },
        "999",
    )[0]

    assert event.kind == "command"
    assert event.command == "help"
    assert event.text == "details"


def _provision_connection(client, run_jobs) -> dict:
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support Agent"}).json()
    response = client.post(
        "/v1/connections/telegram",
        json={
            "customer_id": customer["id"],
            "agent_id": agent["id"],
            "display_name": "Acme Support",
        },
    )
    assert response.status_code == 201
    connection = response.json()
    assert connection["status"] == "provisioning"
    assert connection["channel"] == "telegram"
    run_jobs()
    return client.get(f"/v1/connections/{connection['id']}").json()


def test_provision_telegram_connection(client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    assert connection["status"] == "active"
    assert connection["address"].startswith("@")
    assert not any(k.startswith("provider") for k in connection)


def test_email_and_telegram_connections_coexist(client, run_jobs):
    telegram = _provision_connection(client, run_jobs)
    customer = client.post("/v1/customers", json={"name": "Beta"}).json()
    agent = client.post("/v1/agents", json={"name": "Beta Agent"}).json()
    email = client.post(
        "/v1/connections/email",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    ).json()
    run_jobs()
    email = client.get(f"/v1/connections/{email['id']}").json()
    assert email["status"] == "active"
    assert email["channel"] == "email"
    assert telegram["channel"] == "telegram"


def test_inbound_update_to_event(app, client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    provider = _telegram_provider(app)

    payload = provider.webhook_payload(chat_id=777, text="Where is my order?")
    response = client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    assert response.status_code == 204
    run_jobs()

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1
    data = events[0]["data"]
    assert data["connection_id"] == connection["id"]
    message = data["message"]
    assert message["channel"] == "telegram"
    assert message["direction"] == "inbound"
    assert message["text"] == "Where is my order?"
    assert message["sender"]["address"] == "customer"
    assert not any(k.startswith("provider") for k in message)

    # duplicate delivery of the same update must not create a second message
    second = client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    assert second.status_code == 204
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1


def test_updates_in_same_chat_share_a_conversation(app, client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    provider = _telegram_provider(app)

    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=42, text="first"),
    )
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=42, text="second"),
    )
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=43, text="other chat"),
    )
    run_jobs()

    conversations = client.get(
        "/v1/conversations", params={"connection_id": connection["id"]}
    ).json()
    assert len(conversations) == 2


def test_reply_flow(app, client, run_jobs):
    _provision_connection(client, run_jobs)
    provider = _telegram_provider(app)

    payload = provider.webhook_payload(chat_id=777, text="Question")
    client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[0]["data"]["message"]["id"]

    response = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "On its way"})
    assert response.status_code == 201
    reply = response.json()
    assert reply["status"] == "queued"
    assert reply["channel"] == "telegram"

    run_jobs()
    assert len(provider.replies) == 1
    assert provider.replies[0]["chat_id"] == "777"
    assert provider.replies[0]["in_reply_to"] == str(payload["message"]["message_id"])
    assert provider.replies[0]["text"] == "On its way"

    sent = client.get(f"/v1/messages/{reply['id']}").json()
    assert sent["status"] == "sent"

    thread = client.get(f"/v1/conversations/{reply['conversation_id']}/messages").json()
    assert [m["direction"] for m in thread] == ["inbound", "outbound"]


def test_webhook_secret_enforced(client):
    import json as jsonlib

    from comm_gateway.config import Settings
    from comm_gateway.main import create_app
    from fastapi.testclient import TestClient

    secured = FakeTelegramProvider(webhook_secret="s3cret")
    app = create_app(
        Settings(
            database_url="sqlite://",
            bootstrap_api_key="comm_test_key",
            inline_worker=False,
        ),
        providers={secured.name: secured},
    )
    secured_client = TestClient(app, headers={"Authorization": "Bearer comm_test_key"})

    payload = secured.webhook_payload()
    missing = secured_client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    assert missing.status_code == 400

    ok = secured_client.post(
        "/internal/providers/fake-telegram/webhooks",
        content=jsonlib.dumps(payload),
        headers={SECRET_HEADER: "s3cret", "content-type": "application/json"},
    )
    assert ok.status_code == 204


def test_non_text_updates_are_ignored(app, client, run_jobs):
    _provision_connection(client, run_jobs)
    # A location update carries neither text/caption nor an attachment we extract,
    # so it is unrepresentable and dropped (photos now come through as media).
    payload = {
        "update_id": 999999,
        "message": {
            "message_id": 1,
            "chat": {"id": 55, "type": "private"},
            "date": 1_752_400_000,
            "location": {"latitude": 1.0, "longitude": 2.0},
        },
    }
    response = client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    assert response.status_code == 204
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert events == []
