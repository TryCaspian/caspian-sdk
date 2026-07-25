from fastapi.testclient import TestClient


def test_sandbox_project_and_zero_config_flow(app, run_jobs):
    client = TestClient(app)

    created = client.post("/v1/projects/sandbox", json={"name": "my-agent"})
    assert created.status_code == 201
    api_key = created.json()["api_key"]
    assert api_key.startswith("comm_sandbox_")

    authed = TestClient(app, headers={"Authorization": f"Bearer {api_key}"})

    # zero-ceremony connect: no customer_id or agent_id
    connection = authed.post("/v1/connections/email", json={"display_name": "My Agent"}).json()
    assert connection["status"] == "provisioning"
    run_jobs()
    connection = authed.get(f"/v1/connections/{connection['id']}").json()
    assert connection["status"] == "active"

    # connect is idempotent: a second zero-config call returns the same connection
    second = authed.post("/v1/connections/email", json={}).json()
    assert second["id"] == connection["id"]
    assert second["address"] == connection["address"]

    # test-email delivers through the normal inbound pipeline
    delivered = authed.post("/v1/test-emails", json={"text": "are you alive?"})
    assert delivered.status_code == 202
    run_jobs()
    events = authed.get("/v1/events", params={"type": "message.received"}).json()
    assert len(events) == 1
    message = events[0]["data"]["message"]
    assert message["text"] == "are you alive?"
    assert message["sender"]["address"] == "tester@sandbox.comm.local"

    # sandbox projects are isolated from each other
    other_key = client.post("/v1/projects/sandbox", json={}).json()["api_key"]
    other = TestClient(app, headers={"Authorization": f"Bearer {other_key}"})
    assert other.get("/v1/events").json() == []
    assert other.get("/v1/connections").json() == []


def test_partial_scope_rejected(app, client):
    response = client.post("/v1/connections/email", json={"customer_id": "cus_x"})
    assert response.status_code == 422


def test_sandbox_rate_limit(app):
    from comm_gateway.routes.api import SANDBOX_RATE_LIMIT, _sandbox_requests

    _sandbox_requests.clear()
    client = TestClient(app)
    for _ in range(SANDBOX_RATE_LIMIT):
        assert client.post("/v1/projects/sandbox", json={}).status_code == 201
    assert client.post("/v1/projects/sandbox", json={}).status_code == 429
    _sandbox_requests.clear()


def test_skill_md_served(client):
    response = client.get("/SKILL.md")
    assert response.status_code == 200
    assert "connect_email" in response.text
    assert "/v1/billing" in response.text  # balance check
    assert "dashboard" in response.text.lower()  # credit is added in the dashboard
    assert client.get("/llms.txt").status_code == 200
