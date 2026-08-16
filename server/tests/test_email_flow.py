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


def test_send_message_targets_email_counterparty():
    """Regression: a proactive send on email must target the counterparty's
    address, not the conversation's Message-ID thread key.

    Bug: ``_send_message`` built ``to=(conversation.provider_thread_id,)``. For
    email that thread key is not a deliverable address, so SES received an empty
    recipient and the mail silently never left (the route still returned 201).
    The destination must resolve to the latest inbound sender — while
    thread-routed channels (Telegram/Slack/Discord) keep their thread id.
    """
    from comm_gateway.jobs import _send_destination, _send_message
    from comm_gateway.models import Base, Connection, Conversation, Message
    from comm_gateway.providers.fakes.fake import FakeEmailProvider
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        conn = Connection(
            id="conn_email", project_id="p", customer_id="c", agent_id="a",
            channel="email", provider="fake", provider_resource_id="inbox@acme.test",
            address="inbox@acme.test", status="active",
        )
        conv = Conversation(
            id="conv_email", project_id="p", connection_id="conn_email",
            provider_thread_id="<thread-root@acme.test>",  # a Message-ID, NOT an address
        )
        inbound = Message(
            id="msg_in", project_id="p", conversation_id="conv_email",
            connection_id="conn_email", channel="email", direction="inbound",
            status="received", sender_address="lead@customer.test",
        )
        outbound = Message(
            id="msg_out", project_id="p", conversation_id="conv_email",
            connection_id="conn_email", channel="email", direction="outbound",
            status="queued", text="Following up",
        )
        session.add_all([conn, conv, inbound, outbound])
        session.commit()

        fake = FakeEmailProvider()
        _send_message(session, {"fake": fake}, {"message_id": "msg_out"})

        # The whole point: delivered to the counterparty, not the thread key.
        assert fake.sent[0]["to"] == ["lead@customer.test"]
        assert fake.sent[0]["to"] != ["<thread-root@acme.test>"]
        assert session.get(Message, "msg_out").status == "sent"

        # Thread-routed channels still route by provider_thread_id (chat id).
        tg_conn = Connection(
            id="conn_tg", project_id="p", customer_id="c", agent_id="a",
            channel="telegram", provider="telegram", provider_resource_id="123",
            address="bot", status="active",
        )
        tg_conv = Conversation(
            id="conv_tg", project_id="p", connection_id="conn_tg",
            provider_thread_id="999888",
        )
        session.add_all([tg_conn, tg_conv])
        session.commit()
        assert _send_destination(session, tg_conn, tg_conv) == "999888"
