from fastapi.testclient import TestClient


def _provision_connection(client, run_jobs) -> dict:
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support Agent"}).json()
    response = client.post(
        "/v1/connections/email",
        json={
            "customer_id": customer["id"],
            "agent_id": agent["id"],
            "display_name": "Acme Support",
        },
    )
    assert response.status_code == 201
    connection = response.json()
    assert connection["status"] == "provisioning"
    run_jobs()
    return client.get(f"/v1/connections/{connection['id']}").json()


def test_auth_required(app):
    anonymous = TestClient(app)
    assert anonymous.get("/v1/customers").status_code == 401
    bad_key = TestClient(app, headers={"Authorization": "Bearer wrong"})
    assert bad_key.get("/v1/customers").status_code == 401


def test_provision_email_connection(client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    assert connection["status"] == "active"
    assert connection["address"].endswith("@sandbox.comm.local")
    assert "provider" not in connection
    assert not any(k.startswith("provider") for k in connection)


def test_inbound_message_to_event(app, client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    provider = app.state.providers["fake"]
    inbox_id = next(iter(provider.inboxes))

    payload = provider.webhook_payload(inbox_id, subject="Order status", text="Where is it?")
    assert client.post("/internal/providers/fake/webhooks", json=payload).status_code == 204
    run_jobs()

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1
    data = events[0]["data"]
    assert data["connection_id"] == connection["id"]
    message = data["message"]
    assert message["direction"] == "inbound"
    assert message["subject"] == "Order status"
    assert message["text"] == "Where is it?"
    assert message["sender"]["address"] == "customer@example.com"
    assert not any(k.startswith("provider") for k in message)

    # duplicate delivery of the same provider event must not create a second message
    assert client.post("/internal/providers/fake/webhooks", json=payload).status_code == 204
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1

    conversations = client.get(
        "/v1/conversations", params={"connection_id": connection["id"]}
    ).json()
    assert len(conversations) == 1


def test_reply_flow(app, client, run_jobs):
    connection = _provision_connection(client, run_jobs)
    provider = app.state.providers["fake"]
    inbox_id = next(iter(provider.inboxes))

    payload = provider.webhook_payload(inbox_id, subject="Question")
    client.post("/internal/providers/fake/webhooks", json=payload)
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[0]["data"]["message"]["id"]

    response = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "On its way"})
    assert response.status_code == 201
    reply = response.json()
    assert reply["status"] == "queued"
    assert reply["direction"] == "outbound"
    assert reply["subject"] == "Re: Question"
    assert reply["sender"]["address"] == connection["address"]

    run_jobs()
    assert len(provider.replies) == 1
    assert provider.replies[0]["inbox_id"] == inbox_id
    assert provider.replies[0]["in_reply_to"] == payload["message"]["message_id"]
    assert provider.replies[0]["text"] == "On its way"

    sent = client.get(f"/v1/messages/{reply['id']}").json()
    assert sent["status"] == "sent"
    sent_events = client.get("/v1/events", params={"type": "message.sent"}).json()
    assert len(sent_events) == 1

    thread = client.get(f"/v1/conversations/{reply['conversation_id']}/messages").json()
    assert [m["direction"] for m in thread] == ["inbound", "outbound"]


def test_reply_requires_inbound_target(client, run_jobs, app):
    connection = _provision_connection(client, run_jobs)
    provider = app.state.providers["fake"]
    inbox_id = next(iter(provider.inboxes))
    client.post("/internal/providers/fake/webhooks", json=provider.webhook_payload(inbox_id))
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[0]["data"]["message"]["id"]
    reply = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "hi"}).json()
    run_jobs()

    response = client.post(f"/v1/messages/{reply['id']}/reply", json={"text": "again"})
    assert response.status_code == 400
    assert connection["id"]


def test_unknown_provider_webhook_404(client):
    response = client.post("/internal/providers/nonexistent/webhooks", json={})
    assert response.status_code == 404
