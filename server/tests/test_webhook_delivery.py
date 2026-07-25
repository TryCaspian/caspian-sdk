"""Outbound event webhooks: events are both pollable AND pushed to the agent."""

import hashlib
import hmac
import json

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _app():
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    provider = FakeEmailProvider()
    app = create_app(settings, providers={provider.name: provider})
    return app, TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"}), provider


def _run(app):
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def test_events_are_pushed_and_signed(monkeypatch):
    app, client, provider = _app()
    delivered = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

    def _fake_post(url, content=None, headers=None, timeout=None):
        delivered.append({"url": url, "body": content, "headers": headers})
        return _Resp()

    import comm_gateway.jobs as jobs

    monkeypatch.setattr(jobs.httpx, "post", _fake_post)

    client.put("/v1/webhook", json={"url": "https://agent.example/hook", "secret": "shh"})
    # Provision an email connection -> emits connection.active, which should push.
    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "A"}).json()
    client.post("/v1/connections/email", json={"customer_id": cust["id"], "agent_id": agent["id"]})
    _run(app)

    assert delivered, "no webhook delivered"
    hook = delivered[-1]
    assert hook["url"] == "https://agent.example/hook"
    body = json.loads(hook["body"])
    assert body["type"] == "connection.active"
    # Signature verifies with the shared secret.
    expected = "sha256=" + hmac.new(b"shh", hook["body"], hashlib.sha256).hexdigest()
    assert hook["headers"]["x-caspian-signature"] == expected


def test_no_webhook_configured_means_polling_only(monkeypatch):
    app, client, provider = _app()
    called = []
    import comm_gateway.jobs as jobs

    monkeypatch.setattr(jobs.httpx, "post", lambda *a, **k: called.append(1))

    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "A"}).json()
    client.post("/v1/connections/email", json={"customer_id": cust["id"], "agent_id": agent["id"]})
    _run(app)

    assert called == []  # nothing pushed
    # ...but the event is still pollable.
    events = client.get("/v1/events", params={"type": "connection.active"}).json()
    assert len(events) == 1


def test_webhook_rejects_internal_url(client):
    # SSRF guard: developer/sandbox key cannot point the webhook at internal IPs.
    for bad in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/x",
                "http://localhost/x", "ftp://example.com/x"):
        r = client.put("/v1/webhook", json={"url": bad})
        assert r.status_code == 422, f"{bad} should be rejected, got {r.status_code}"
    # A public URL is still accepted.
    assert client.put("/v1/webhook", json={"url": "https://agent.example/hook"}).status_code == 200
