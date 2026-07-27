"""Per-connection capability manifest: an agent declares what it wants enabled."""

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from comm_gateway.providers.fakes.fake_telegram import FakeTelegramProvider
from comm_gateway.providers.fakes.fake_telegram_user import FakeTelegramUserProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _make(providers):
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    app = create_app(settings, providers={p.name: p for p in providers})
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    return app, client


def _run(app):
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def _customer_agent(client):
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Support"}).json()
    return customer["id"], agent["id"]


def test_omitted_manifest_grants_full_ceiling():
    app, client = _make([FakeTelegramProvider()])
    cid, aid = _customer_agent(client)
    conn = client.post(
        "/v1/connections/telegram", json={"customer_id": cid, "agent_id": aid}
    ).json()
    # Full Bot API ceiling, sorted.
    assert set(conn["capabilities"]) == set(FakeTelegramProvider.capabilities)


def test_manifest_narrows_grant():
    app, client = _make([FakeTelegramProvider()])
    cid, aid = _customer_agent(client)
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": cid, "agent_id": aid, "capabilities": ["receive"]},
    ).json()
    # receive requested; reply always granted as baseline; send NOT granted.
    assert set(conn["capabilities"]) == {"receive", "reply"}
    assert "send" not in conn["capabilities"]


def test_send_blocked_when_not_in_manifest():
    app, client = _make([FakeTelegramProvider()])
    cid, aid = _customer_agent(client)
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": cid, "agent_id": aid, "capabilities": ["receive"]},
    ).json()
    _run(app)
    provider = app.state.providers["fake-telegram"]
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=5, text="hi"),
    )
    _run(app)
    conv = client.get("/v1/conversations", params={"connection_id": conn["id"]}).json()[0]

    # Manifest excluded send -> 422 even though the Bot API supports it.
    blocked = client.post(f"/v1/conversations/{conv['id']}/messages", json={"text": "x"})
    assert blocked.status_code == 422
    assert "does not grant" in blocked.json()["detail"]


def test_send_allowed_when_in_manifest():
    app, client = _make([FakeTelegramProvider()])
    cid, aid = _customer_agent(client)
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": cid, "agent_id": aid, "capabilities": ["receive", "send"]},
    ).json()
    _run(app)
    provider = app.state.providers["fake-telegram"]
    client.post(
        "/internal/providers/fake-telegram/webhooks",
        json=provider.webhook_payload(chat_id=5, text="hi"),
    )
    _run(app)
    conv = client.get("/v1/conversations", params={"connection_id": conn["id"]}).json()[0]

    ok = client.post(f"/v1/conversations/{conv['id']}/messages", json={"text": "x"})
    assert ok.status_code == 201


def test_requesting_capability_beyond_ceiling_is_422():
    app, client = _make([FakeTelegramProvider()])
    cid, aid = _customer_agent(client)
    # The Bot API cannot initiate; asking for it at connect time is rejected.
    response = client.post(
        "/v1/connections/telegram",
        json={"customer_id": cid, "agent_id": aid, "capabilities": ["initiate"]},
    )
    assert response.status_code == 422
    assert "cannot provide" in response.json()["detail"]
    assert "initiate" in response.json()["detail"]


def test_unknown_capability_is_422():
    app, client = _make([FakeTelegramProvider()])
    cid, aid = _customer_agent(client)
    response = client.post(
        "/v1/connections/telegram",
        json={"customer_id": cid, "agent_id": aid, "capabilities": ["teleport"]},
    )
    assert response.status_code == 422
    assert "Unknown capabilities" in response.json()["detail"]


def test_user_account_can_grant_initiate_but_manifest_can_withhold_it():
    app, client = _make([FakeTelegramUserProvider()])
    cid, aid = _customer_agent(client)
    # A cautious outreach agent takes send but deliberately NOT initiate.
    conn = client.post(
        "/v1/connections/telegram",
        json={"customer_id": cid, "agent_id": aid, "capabilities": ["send"]},
    ).json()
    _run(app)
    assert "initiate" not in conn["capabilities"]

    blocked = client.post(
        f"/v1/connections/{conn['id']}/initiate",
        json={"recipient": "@prospect", "text": "hi"},
    )
    assert blocked.status_code == 422


def test_email_manifest_defaults_to_receive_reply():
    app, client = _make([FakeEmailProvider()])
    cid, aid = _customer_agent(client)
    conn = client.post(
        "/v1/connections/email", json={"customer_id": cid, "agent_id": aid}
    ).json()
    assert set(conn["capabilities"]) == {"receive", "reply"}
