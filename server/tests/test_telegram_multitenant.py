"""Multi-tenant Telegram: each developer brings their own bot, no overlap."""

from comm_gateway.providers.telegram import SECRET_HEADER
from fastapi.testclient import TestClient

BOT_A = "9111111:AAA-fake-token-a"
BOT_B = "9222222:BBB-fake-token-b"


def _sandbox_client(app) -> TestClient:
    key = TestClient(app).post("/v1/projects/sandbox", json={}).json()["api_key"]
    return TestClient(app, headers={"Authorization": f"Bearer {key}"})


def _connect_bot(client, run_jobs, bot_token: str) -> dict:
    connection = client.post(
        "/v1/connections/telegram", json={"bot_token": bot_token}
    ).json()
    run_jobs()
    return client.get(f"/v1/connections/{connection['id']}").json()


def test_two_developers_two_bots_no_overlap(app, run_jobs):
    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    dev_a = _sandbox_client(app)
    dev_b = _sandbox_client(app)
    provider = app.state.providers["fake-telegram"]

    conn_a = _connect_bot(dev_a, run_jobs, BOT_A)
    conn_b = _connect_bot(dev_b, run_jobs, BOT_B)
    assert conn_a["status"] == "active"
    assert conn_b["status"] == "active"

    # inbound for bot A goes through A's scoped webhook with A's secret
    anonymous = TestClient(app)

    def deliver(bot_token: str, text: str):
        bot_id = bot_token.split(":")[0]
        # find the per-connection secret the gateway stored
        from comm_gateway.models import Connection

        with app.state.session_factory() as session:
            connection = (
                session.query(Connection)
                .filter(Connection.provider_resource_id == bot_id)
                .one()
            )
            secret = connection.provider_credentials["webhook_secret"]
        payload = provider.webhook_payload(text=text)
        return anonymous.post(
            f"/internal/providers/fake-telegram/webhooks/{bot_id}",
            json=payload,
            headers={SECRET_HEADER: secret},
        )

    assert deliver(BOT_A, "hello agent A").status_code == 204
    assert deliver(BOT_B, "hello agent B").status_code == 204
    run_jobs()

    events_a = dev_a.get("/v1/events", params={"type": "message.received"}).json()
    events_b = dev_b.get("/v1/events", params={"type": "message.received"}).json()
    assert [e["data"]["message"]["text"] for e in events_a] == ["hello agent A"]
    assert [e["data"]["message"]["text"] for e in events_b] == ["hello agent B"]
    assert events_a[0]["data"]["connection_id"] == conn_a["id"]
    assert events_b[0]["data"]["connection_id"] == conn_b["id"]


def test_same_bot_cannot_be_connected_twice(app, run_jobs):
    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    dev_a = _sandbox_client(app)
    dev_b = _sandbox_client(app)
    _connect_bot(dev_a, run_jobs, BOT_A)

    response = dev_b.post("/v1/connections/telegram", json={"bot_token": BOT_A})
    assert response.status_code == 409
    assert "already connected" in response.json()["detail"]


def test_reconnect_same_bot_same_scope_is_idempotent(app, run_jobs):
    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    dev = _sandbox_client(app)
    first = _connect_bot(dev, run_jobs, BOT_A)
    second = dev.post("/v1/connections/telegram", json={"bot_token": BOT_A}).json()
    assert second["id"] == first["id"]


def test_wrong_secret_rejected(app, run_jobs):
    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    dev = _sandbox_client(app)
    provider = app.state.providers["fake-telegram"]
    _connect_bot(dev, run_jobs, BOT_A)
    bot_id = BOT_A.split(":")[0]

    response = TestClient(app).post(
        f"/internal/providers/fake-telegram/webhooks/{bot_id}",
        json=provider.webhook_payload(text="spoofed"),
        headers={SECRET_HEADER: "wrong-secret"},
    )
    assert response.status_code == 400


def test_unknown_bot_resource_404(app):
    response = TestClient(app).post(
        "/internal/providers/fake-telegram/webhooks/999999",
        json={"update_id": 1},
    )
    assert response.status_code == 404


def test_credentials_never_leak_in_responses(app, run_jobs):
    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    dev = _sandbox_client(app)
    connection = _connect_bot(dev, run_jobs, BOT_B)
    listing = dev.get("/v1/connections").json()
    blob = str(connection) + str(listing)
    assert BOT_B not in blob
    assert "bot_token" not in blob
    assert "webhook_secret" not in blob
