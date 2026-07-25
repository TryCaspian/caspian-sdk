def test_add_domain_returns_records(client):
    response = client.post("/v1/domains", json={"domain": "agents.acme.com"})
    assert response.status_code == 201
    domain = response.json()
    assert domain["status"] == "pending_dns"
    types = sorted(r["type"] for r in domain["dns_records"])
    assert types == ["CNAME", "MX"]
    assert any(r["name"] == "agents.acme.com" for r in domain["dns_records"])


def test_root_domain_rejected(client):
    response = client.post("/v1/domains", json={"domain": "acme.com"})
    assert response.status_code == 422
    assert "subdomain" in response.json()["detail"]


def test_invalid_domain_rejected(client):
    assert client.post("/v1/domains", json={"domain": "not a domain"}).status_code == 422


def test_domain_verifies_and_activates_inbound(app, client):
    domain = client.post("/v1/domains", json={"domain": "agents.acme.com"}).json()
    # fake provider verifies instantly on read
    checked = client.get(f"/v1/domains/{domain['id']}").json()
    assert checked["status"] == "active"
    assert "agents.acme.com" in app.state.providers["fake"].inbound_domains
    events = client.get("/v1/events", params={"type": "domain.verified"}).json()
    assert len(events) == 1


def test_domain_claim_is_exclusive(app, client):
    from comm_gateway.routes.api import _sandbox_requests
    from fastapi.testclient import TestClient

    _sandbox_requests.clear()
    client.post("/v1/domains", json={"domain": "agents.acme.com"})
    other_key = TestClient(app).post("/v1/projects/sandbox", json={}).json()["api_key"]
    other = TestClient(app, headers={"Authorization": f"Bearer {other_key}"})
    response = other.post("/v1/domains", json={"domain": "agents.acme.com"})
    assert response.status_code == 409


def test_connect_email_on_custom_domain(client, run_jobs):
    domain = client.post("/v1/domains", json={"domain": "agents.acme.com"}).json()
    client.get(f"/v1/domains/{domain['id']}")  # triggers verification

    connection = client.post(
        "/v1/connections/email",
        json={"display_name": "Acme Support", "domain": "agents.acme.com"},
    ).json()
    run_jobs()
    connection = client.get(f"/v1/connections/{connection['id']}").json()
    assert connection["status"] == "active"
    assert connection["address"].endswith("@agents.acme.com")

    # a default-domain connection for the same scope is separate
    default = client.post("/v1/connections/email", json={}).json()
    assert default["id"] != connection["id"]


def test_connect_on_unverified_domain_rejected(client):
    client.post("/v1/domains", json={"domain": "agents.beta.com"})
    # not verified yet (no GET to trigger the fake's instant verification)
    response = client.post("/v1/connections/email", json={"domain": "agents.beta.com"})
    assert response.status_code in (404, 409)


def test_connect_on_unowned_domain_rejected(client):
    response = client.post("/v1/connections/email", json={"domain": "agents.stranger.com"})
    assert response.status_code == 404


def test_zone_file(client):
    domain = client.post("/v1/domains", json={"domain": "agents.acme.com"}).json()
    response = client.get(f"/v1/domains/{domain['id']}/zone-file")
    assert response.status_code == 200
    assert "IN MX 10" in response.text
    assert "IN CNAME" in response.text


def test_username_on_custom_domain(client, run_jobs):
    domain = client.post("/v1/domains", json={"domain": "agents.acme.com"}).json()
    client.get(f"/v1/domains/{domain['id']}")

    connection = client.post(
        "/v1/connections/email",
        json={"domain": "agents.acme.com", "username": "kernel"},
    ).json()
    run_jobs()
    connection = client.get(f"/v1/connections/{connection['id']}").json()
    assert connection["address"] == "kernel@agents.acme.com"


def test_username_on_default_domain(client, run_jobs):
    # A readable, developer-chosen name works on the platform domain (no custom domain).
    connection = client.post("/v1/connections/email", json={"username": "scout"}).json()
    run_jobs()
    connection = client.get(f"/v1/connections/{connection['id']}").json()
    assert connection["address"] == "scout@sandbox.comm.local"


def test_username_collision_on_default_domain_suggests(app, client, run_jobs):
    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    client.post("/v1/connections/email", json={"username": "scout"})
    run_jobs()

    customer = client.post("/v1/customers", json={"name": "Other"}).json()
    agent = client.post("/v1/agents", json={"name": "Other"}).json()
    response = client.post(
        "/v1/connections/email",
        json={"username": "scout", "customer_id": customer["id"], "agent_id": agent["id"]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["suggestions"], "expected readable alternatives"
    assert all("scout" in s for s in detail["suggestions"])
    assert "scout1" in detail["suggestions"]  # readable, no random hex


def test_username_collision_rejected(app, client, run_jobs):

    from comm_gateway.routes.api import _sandbox_requests

    _sandbox_requests.clear()
    domain = client.post("/v1/domains", json={"domain": "agents.acme.com"}).json()
    client.get(f"/v1/domains/{domain['id']}")
    client.post("/v1/connections/email", json={"domain": "agents.acme.com", "username": "kernel"})
    run_jobs()

    customer = client.post("/v1/customers", json={"name": "Other Team"}).json()
    agent = client.post("/v1/agents", json={"name": "Other Agent"}).json()
    response = client.post(
        "/v1/connections/email",
        json={
            "domain": "agents.acme.com",
            "username": "kernel",
            "customer_id": customer["id"],
            "agent_id": agent["id"],
        },
    )
    assert response.status_code == 409
