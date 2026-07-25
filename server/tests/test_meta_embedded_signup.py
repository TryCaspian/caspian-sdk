"""End-to-end WhatsApp Embedded Signup through the HTTP API, mocked Graph."""

import hashlib
import hmac
import json

import httpx
import pytest
from comm_gateway import crypto
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.models import Connection
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _fund_project(app, api_key: str, email: str) -> None:
    """Give a project credit so paid-channel (WhatsApp) onboarding passes the gate."""
    from comm_gateway.auth import hash_key
    from comm_gateway.crypto import _encrypt
    from comm_gateway.models import ApiKey, DashboardAccount
    from sqlalchemy import select

    with app.state.session_factory() as s:
        pid = s.execute(
            select(ApiKey.project_id).where(ApiKey.key_hash == hash_key(api_key))
        ).scalar_one()
        s.add(DashboardAccount(
            email=email, project_id=pid,
            api_key_enc=_encrypt({"api_key": api_key}), credit_cents=10000,
        ))
        s.commit()
APP_SECRET = "app-secret-xyz"
PHONE_ID = "PN_100"
WABA_ID = "WABA_200"


def _graph_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/oauth/access_token"):
        return httpx.Response(200, json={"access_token": "BUSINESS_TOKEN"})
    if path.endswith("/subscribed_apps"):
        assert request.headers["authorization"] == "Bearer BUSINESS_TOKEN"
        return httpx.Response(200, json={"success": True})
    if path.endswith("/register"):
        return httpx.Response(200, json={"success": True})
    if path.endswith("/messages"):
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})
    return httpx.Response(404, json={"error": {"message": "unexpected " + path}})


@pytest.fixture()
def app():
    settings = Settings(
        database_url="sqlite://",
        providers="meta-whatsapp",
        bootstrap_api_key=API_KEY,
        inline_worker=False,
        credentials_key=Fernet.generate_key().decode(),
        meta_app_id="APP123",
        meta_app_secret=APP_SECRET,
        meta_es_config_id="CFG456",
        public_base_url="https://gw.test",
        meta_wa_app_secret=APP_SECRET,
        meta_wa_verify_token="VERIFY_TOK",
    )
    application = create_app(settings)
    # Back the provider's Graph client with the mock transport.
    provider = application.state.providers["meta-whatsapp"]
    provider._client = httpx.Client(
        base_url="https://graph.facebook.com/v21.0",
        transport=httpx.MockTransport(_graph_handler),
        timeout=5.0,
    )
    # WhatsApp is a paid channel; fund the bootstrap project so onboarding passes
    # the credit gate (these tests exercise signup, not billing).
    _fund_project(application, API_KEY, "wa-dev@example.com")
    yield application
    crypto.configure_cipher("")


@pytest.fixture()
def client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def _run_jobs(app):
    from comm_gateway.jobs import run_pending_jobs

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def _session_token(client) -> str:
    r = client.post("/v1/connections/whatsapp/onboarding-session", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["launcher_url"].startswith("https://gw.test/connect/whatsapp?session=")
    return body["session"]


def test_onboarding_session_requires_config():
    # An app without Embedded Signup config must 400 the session endpoint.
    settings = Settings(
        database_url="sqlite://", providers="meta-whatsapp",
        bootstrap_api_key=API_KEY, inline_worker=False, meta_wa_app_secret="x",
    )
    app = create_app(settings)
    c = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = c.post("/v1/connections/whatsapp/onboarding-session", json={})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]


def test_session_mint_and_launcher_render(client, app):
    token = _session_token(client)
    page = TestClient(app).get(f"/connect/whatsapp?session={token}")
    assert page.status_code == 200
    assert "APP123" in page.text and "CFG456" in page.text
    assert "WA_EMBEDDED_SIGNUP" in page.text


def test_bad_session_rejected(client):
    r = client.post(
        "/v1/connections/whatsapp/embedded-signup",
        json={"session": "forged.token", "code": "c", "phone_number_id": PHONE_ID,
              "waba_id": WABA_ID},
    )
    assert r.status_code == 401


def test_full_signup_creates_active_connection(client, app):
    token = _session_token(client)
    r = TestClient(app).post(
        "/v1/connections/whatsapp/embedded-signup",
        json={"session": token, "code": "the-code", "phone_number_id": PHONE_ID,
              "waba_id": WABA_ID},
    )
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["channel"] == "whatsapp"
    assert conn["status"] == "provisioning"

    assert _run_jobs(app) >= 1  # provision runs subscribe + register
    active = client.get(f"/v1/connections/{conn['id']}").json()
    assert active["status"] == "active"
    assert active["address"] == PHONE_ID

    # Credentials are encrypted at rest, keyed by phone_number_id for routing.
    with app.state.session_factory() as session:
        row = session.query(Connection).filter(
            Connection.provider_resource_id == PHONE_ID
        ).one()
        assert set(row.provider_credentials) == {"__enc__"}
        assert crypto.read_credentials(row)["access_token"] == "BUSINESS_TOKEN"


def test_same_number_same_scope_is_idempotent(client, app):
    payload = {"session": _session_token(client), "code": "the-code",
               "phone_number_id": PHONE_ID, "waba_id": WABA_ID}
    first = TestClient(app).post("/v1/connections/whatsapp/embedded-signup", json=payload)
    assert first.status_code == 201
    _run_jobs(app)
    # Re-running the same scope + number returns the same connection, not a new one.
    payload["session"] = _session_token(client)
    second = TestClient(app).post("/v1/connections/whatsapp/embedded-signup", json=payload)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_same_number_different_project_conflicts(client, app):
    # First project (bootstrap) claims the number.
    first = TestClient(app).post(
        "/v1/connections/whatsapp/embedded-signup",
        json={"session": _session_token(client), "code": "the-code",
              "phone_number_id": PHONE_ID, "waba_id": WABA_ID},
    )
    assert first.status_code == 201
    _run_jobs(app)

    # A second, independent project tries to claim the same WhatsApp number → 409.
    other_key = TestClient(app).post("/v1/projects/sandbox", json={}).json()["api_key"]
    _fund_project(app, other_key, "wa-other@example.com")  # so it reaches the conflict check
    other = TestClient(app, headers={"Authorization": f"Bearer {other_key}"})
    other_session = other.post(
        "/v1/connections/whatsapp/onboarding-session", json={}
    ).json()["session"]
    conflict = TestClient(app).post(
        "/v1/connections/whatsapp/embedded-signup",
        json={"session": other_session, "code": "the-code",
              "phone_number_id": PHONE_ID, "waba_id": WABA_ID},
    )
    assert conflict.status_code == 409


def test_get_webhook_challenge(app):
    anon = TestClient(app)
    ok = anon.get(
        "/internal/providers/meta-whatsapp/webhooks",
        params={"hub.mode": "subscribe", "hub.verify_token": "VERIFY_TOK", "hub.challenge": "42"},
    )
    # A matching, configured verify_token echoes the challenge.
    assert ok.status_code == 200
    assert ok.text == "42"

    # Fail-closed: a wrong token, or an empty token against a configured secret,
    # must be rejected (an empty token no longer "matches" an unset secret).
    for tok in ("wrong", ""):
        bad = anon.get(
            "/internal/providers/meta-whatsapp/webhooks",
            params={"hub.mode": "subscribe", "hub.verify_token": tok, "hub.challenge": "42"},
        )
        assert bad.status_code == 403


def test_meta_verify_fails_closed_when_token_unset():
    # An unset verify_token must NEVER validate, even for an empty incoming token
    # (would otherwise fail open for an unconfigured provider).
    from comm_gateway.providers.meta_whatsapp import MetaWhatsAppProvider

    p = MetaWhatsAppProvider(app_secret="x", verify_token="")
    assert p.meta_verify(
        {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "9"}
    ) is None


def test_inbound_webhook_delivers_message(client, app):
    token = _session_token(client)
    conn = TestClient(app).post(
        "/v1/connections/whatsapp/embedded-signup",
        json={"session": token, "code": "the-code", "phone_number_id": PHONE_ID,
              "waba_id": WABA_ID},
    ).json()
    _run_jobs(app)

    body = json.dumps(
        {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": PHONE_ID},
            "messages": [{"id": "wamid.IN", "from": "+15557778888", "type": "text",
                          "text": {"body": "inbound hi"}}],
        }}]}]}
    ).encode()
    sig = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    resp = TestClient(app).post(
        f"/internal/providers/meta-whatsapp/webhooks/{PHONE_ID}",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 204
    _run_jobs(app)

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    assert [e["data"]["message"]["text"] for e in events] == ["inbound hi"]
    assert events[0]["data"]["connection_id"] == conn["id"]
