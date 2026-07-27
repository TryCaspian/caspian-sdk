"""Capability model + Bot API proactive send, edits, and channel discovery."""

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from comm_gateway.providers.fakes.fake_telegram import FakeTelegramProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _telegram_provider(app) -> FakeTelegramProvider:
    return app.state.providers["fake-telegram"]


def _telegram_connection(client, run_jobs) -> dict:
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support"}).json()
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    ).json()
    run_jobs()
    return client.get(f"/v1/connections/{conn['id']}").json()


def _inbound(app, client, run_jobs, chat_id=777, text="Hi"):
    provider = _telegram_provider(app)
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=chat_id, text=text),
    )
    run_jobs()
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    return events[-1]["data"]["message"]


def test_channels_report_capabilities(client):
    channels = client.get("/v1/channels").json()
    by_channel = {c["channel"]: c for c in channels}
    assert "send" in by_channel["telegram"]["capabilities"]
    assert "group_visibility" in by_channel["telegram"]["capabilities"]
    # Email declares neither proactive send nor initiate in this slice.
    assert "send" not in by_channel["email"]["capabilities"]
    assert "initiate" not in by_channel["telegram"]["capabilities"]


def test_proactive_send_into_conversation(app, client, run_jobs):
    _telegram_connection(client, run_jobs)
    inbound = _inbound(app, client, run_jobs, chat_id=777, text="Hello")
    conversation_id = inbound["conversation_id"]

    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Proactive ping"},
    )
    assert response.status_code == 201
    msg = response.json()
    assert msg["direction"] == "outbound"
    assert msg["channel"] == "telegram"

    run_jobs()
    provider = _telegram_provider(app)
    assert provider.sent[-1]["chat_id"] == "777"
    assert provider.sent[-1]["text"] == "Proactive ping"

    sent = client.get(f"/v1/messages/{msg['id']}").json()
    assert sent["status"] == "sent"


def test_proactive_send_rejected_on_email(app, client, run_jobs):
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support"}).json()
    client.post(
        "/v1/connections/email",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    )
    run_jobs()
    # Drive an inbound email so an email conversation exists.
    email_provider = app.state.providers["fake"]
    inbox_id = next(iter(email_provider.inboxes))
    client.post(
        "/internal/providers/fake/webhooks",
        json=email_provider.webhook_payload(inbox_id, subject="Q", text="hi"),
    )
    run_jobs()
    conversation = client.get("/v1/conversations").json()[0]

    # Email declares no proactive-send capability -> 422, not a silent failure.
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages", json={"text": "ping"}
    )
    assert response.status_code == 422


def test_edit_updates_message_and_emits_event(app, client, run_jobs):
    _telegram_connection(client, run_jobs)
    provider = _telegram_provider(app)

    original = provider.webhook_payload(chat_id=42, text="teh order")
    client.post("/internal/providers/fake-telegram/webhooks", json=original)
    run_jobs()
    received = client.get("/v1/events", params={"type": "message.received"}).json()
    message_id = received[-1]["data"]["message"]["id"]

    edit = {
        "update_id": original["update_id"] + 1000,
        "edited_message": {
            **original["message"],
            "text": "the order",
        },
    }
    client.post("/internal/providers/fake-telegram/webhooks", json=edit)
    run_jobs()

    edited_events = client.get("/v1/events", params={"type": "message.edited"}).json()
    assert len(edited_events) == 1
    assert edited_events[-1]["data"]["message"]["id"] == message_id

    stored = client.get(f"/v1/messages/{message_id}").json()
    assert stored["text"] == "the order"
    # No second inbound message row was created by the edit.
    received_after = client.get("/v1/events", params={"type": "message.received"}).json()
    assert len(received_after) == 1


def test_group_message_carries_chat_type(app, client, run_jobs):
    _telegram_connection(client, run_jobs)
    provider = _telegram_provider(app)
    payload = provider.webhook_payload(chat_id=-1001, text="team ping")
    payload["message"]["chat"]["type"] = "supergroup"
    client.post("/internal/providers/fake-telegram/webhooks", json=payload)
    run_jobs()
    message = client.get("/v1/events", params={"type": "message.received"}).json()[-1]["data"][
        "message"
    ]
    assert message["chat_type"] == "supergroup"


def _user_account_app():
    from comm_gateway.providers.fakes.fake_telegram_user import FakeTelegramUserProvider  # noqa: PLC0415

    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    email = FakeEmailProvider()
    user = FakeTelegramUserProvider()
    app = create_app(settings, providers={email.name: email, user.name: user})
    return app, user


def test_user_account_declares_superset():
    app, _ = _user_account_app()
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    caps = {
        c["provider"]: set(c["capabilities"]) for c in client.get("/v1/channels").json()
    }["fake-telegram-user"]
    assert {"initiate", "backfill", "presence", "read_receipts", "auto_join", "see_bots"} <= caps
    assert "secret_chats" not in caps  # honestly not supported


def test_initiate_cold_starts_conversation():
    app, user = _user_account_app()
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Outreach"}).json()
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    ).json()
    run_pending_jobs(app.state.session_factory, app.state.providers)

    response = client.post(
        f"/v1/connections/{conn['id']}/initiate",
        json={"recipient": "@prospect", "text": "Hi, following up"},
    )
    assert response.status_code == 202
    run_pending_jobs(app.state.session_factory, app.state.providers)

    assert user.initiated[-1]["recipient"] == "@prospect"
    conversations = client.get("/v1/conversations").json()
    assert len(conversations) == 1
    sent = client.get("/v1/events", params={"type": "message.sent"}).json()
    assert sent[-1]["data"]["message"]["text"] == "Hi, following up"


def test_initiate_rejected_on_bot(app, client, run_jobs):
    conn = _telegram_connection(client, run_jobs)
    response = client.post(
        f"/v1/connections/{conn['id']}/initiate",
        json={"recipient": "@prospect", "text": "hi"},
    )
    assert response.status_code == 422


def test_backfill_pulls_history():
    app, user = _user_account_app()
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support"}).json()
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    ).json()
    run_pending_jobs(app.state.session_factory, app.state.providers)

    # cold-start a conversation, then seed pretend history on its chat
    client.post(
        f"/v1/connections/{conn['id']}/initiate",
        json={"recipient": "@prospect", "text": "current"},
    )
    run_pending_jobs(app.state.session_factory, app.state.providers)
    conversation = client.get("/v1/conversations").json()[0]
    # provider_thread_id is internal and never leaves the API; get the chat id
    # from the fake's own record of what initiate() created.
    chat_id = user.initiated[-1]["chat_id"]
    user.seed_history(int(chat_id), ["older 1", "older 2", "older 3"])

    response = client.post(
        f"/v1/conversations/{conversation['id']}/backfill", json={"limit": 10}
    )
    assert response.status_code == 202
    run_pending_jobs(app.state.session_factory, app.state.providers)

    backfilled = client.get("/v1/events", params={"type": "message.backfilled"}).json()
    assert [e["data"]["message"]["text"] for e in backfilled] == ["older 1", "older 2", "older 3"]
    thread = client.get(f"/v1/conversations/{conversation['id']}/messages").json()
    assert any(m["direction"] == "inbound" and m["text"] == "older 1" for m in thread)


def test_backfill_rejected_on_bot(app, client, run_jobs):
    _telegram_connection(client, run_jobs)
    inbound = _inbound(app, client, run_jobs)
    response = client.post(
        f"/v1/conversations/{inbound['conversation_id']}/backfill", json={"limit": 5}
    )
    assert response.status_code == 422
