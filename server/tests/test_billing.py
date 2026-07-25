"""Pay-as-you-go billing: topup, webhook crediting, gates, limits, autopay."""

import hashlib
import hmac
import json
import time

import pytest
from comm_gateway import billing as billing_mod
from comm_gateway.billing import credit_topup
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from comm_gateway.providers.fakes.fake_channels import FakeWhatsAppProvider
from fastapi.testclient import TestClient

API_KEY = "comm_billing_key"
WEBHOOK_SECRET = "whsec_test"


@pytest.fixture()
def app(monkeypatch):
    calls = []

    def fake_stripe(settings, method, path, data=None, idempotency_key=None):
        calls.append({"method": method, "path": path, "data": data or {},
                      "idempotency_key": idempotency_key})
        if path == "/checkout/sessions":
            return {"id": f"cs_test_{len(calls)}", "url": "https://checkout.stripe.com/c/pay/cs_test"}
        if path.startswith("/payment_intents/"):
            return {"id": path.rsplit("/", 1)[1], "payment_method": "pm_test_123"}
        if path == "/payment_intents":
            return {"id": f"pi_auto_{len(calls)}", "status": "succeeded"}
        raise AssertionError(f"unexpected stripe call {path}")

    monkeypatch.setattr(billing_mod, "_stripe", fake_stripe)
    import comm_gateway.routes.billing as billing_routes

    monkeypatch.setattr(billing_routes, "_stripe", fake_stripe)

    settings = Settings(
        database_url="sqlite://",
        bootstrap_api_key=API_KEY,
        inline_worker=False,
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret=WEBHOOK_SECRET,
        billing_dashboard_url="https://dashboard.example.com",
    )
    wa = FakeWhatsAppProvider(from_number="+1999")
    email = FakeEmailProvider()
    application = create_app(settings, providers={wa.name: wa, email.name: email})
    application.state.stripe_calls = calls
    return application


@pytest.fixture()
def client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def _project_id(app):
    from comm_gateway.models import Project
    from sqlalchemy import select

    with app.state.session_factory() as s:
        return s.execute(select(Project.id)).scalar_one()


def _signed(payload: dict) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"Stripe-Signature": f"t={ts},v1={sig}"}


def _checkout_completed(project_id, session_id="cs_live_1", amount=2000, customer="cus_1",
                        intent="pi_1"):
    return {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": session_id, "client_reference_id": project_id, "amount_total": amount,
            "customer": customer, "payment_intent": intent,
        }},
    }


def test_billing_state_starts_empty(client):
    state = client.get("/v1/billing").json()
    assert state["balance_cents"] == 0
    assert state["autopay"]["enabled"] is False
    assert "whatsapp" in state["paid_channels"]


def test_paid_channel_402_carries_payment_options(client):
    r = client.post("/v1/connections/whatsapp", json={})
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["reason"] == "insufficient_credit"
    option = detail["payment_options"][0]
    assert option["type"] == "dashboard"
    assert option["url"].startswith("http")  # points at the dashboard, not a raw link


def test_free_channel_needs_no_credit(client, app):
    r = client.post("/v1/connections/email", json={})
    assert r.status_code == 201


def test_topup_returns_checkout_url(client, app):
    r = client.post("/v1/billing/topup", json={"amount_cents": 2000})
    assert r.status_code == 201
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    call = app.state.stripe_calls[-1]
    assert call["path"] == "/checkout/sessions"
    assert call["data"]["payment_intent_data[setup_future_usage]"] == "off_session"


def test_webhook_credits_once_and_saves_card(client, app):
    project_id = _project_id(app)
    payload, headers = _signed(_checkout_completed(project_id))
    anon = TestClient(app)
    assert anon.post("/internal/billing/stripe/webhooks", content=payload,
                     headers=headers).status_code == 204
    state = client.get("/v1/billing").json()
    assert state["credit_cents"] == 2000
    assert state["autopay"]["payment_method_saved"] is True

    # replay: same session id must not double-credit
    assert anon.post("/internal/billing/stripe/webhooks", content=payload,
                     headers=headers).status_code == 204
    assert client.get("/v1/billing").json()["credit_cents"] == 2000


def test_webhook_rejects_bad_signature(app):
    anon = TestClient(app)
    raw = json.dumps(_checkout_completed("proj_x")).encode()
    r = anon.post("/internal/billing/stripe/webhooks", content=raw,
                  headers={"Stripe-Signature": "t=1,v1=bad"})
    assert r.status_code == 403


def test_webhook_rejects_replayed_old_but_validly_signed_event(app):
    """A correctly-signed event whose timestamp is outside the tolerance window
    must be rejected - otherwise a captured payload could be replayed to credit
    an account twice."""
    anon = TestClient(app)
    raw = json.dumps(_checkout_completed("proj_x")).encode()
    old_ts = str(int(time.time()) - 3600)  # an hour ago, well past tolerance
    sig = hmac.new(
        WEBHOOK_SECRET.encode(), f"{old_ts}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    r = anon.post("/internal/billing/stripe/webhooks", content=raw,
                  headers={"Stripe-Signature": f"t={old_ts},v1={sig}"})
    assert r.status_code == 403


def test_credited_project_can_connect_paid_channel(client, app):
    project_id = _project_id(app)
    with app.state.session_factory() as s:
        credit_topup(s, project_id, "grant", "grant:test", 5000)
    r = client.post("/v1/connections/whatsapp", json={})
    assert r.status_code == 201


def test_autopay_requires_saved_card_and_cap(client, app):
    project_id = _project_id(app)
    with app.state.session_factory() as s:
        credit_topup(s, project_id, "grant", "grant:pm", 1000)
    r = client.put("/v1/billing/autopay",
                   json={"threshold_cents": 500, "topup_cents": 2000})
    assert r.status_code == 422  # no cap
    r = client.put("/v1/billing/autopay",
                   json={"threshold_cents": 500, "topup_cents": 2000,
                         "monthly_cap_cents": 10000})
    assert r.status_code == 409  # no saved card yet

    payload, headers = _signed(_checkout_completed(project_id, session_id="cs_pm"))
    TestClient(app).post("/internal/billing/stripe/webhooks", content=payload, headers=headers)
    r = client.put("/v1/billing/autopay",
                   json={"threshold_cents": 500, "topup_cents": 2000,
                         "monthly_cap_cents": 10000})
    assert r.status_code == 200
    assert r.json()["autopay"]["enabled"] is True


def test_low_balance_triggers_event_and_autopay(client, app):
    project_id = _project_id(app)
    payload, headers = _signed(_checkout_completed(project_id, session_id="cs_low", amount=50))
    TestClient(app).post("/internal/billing/stripe/webhooks", content=payload, headers=headers)
    client.put("/v1/billing/autopay",
               json={"threshold_cents": 500, "topup_cents": 2000, "monthly_cap_cents": 10000})

    before = len(app.state.stripe_calls)
    r = client.post("/v1/connections/whatsapp", json={})
    assert r.status_code == 201  # 50c balance passes the >0 gate
    events = client.get("/v1/events", params={"type": "billing.low_balance"}).json()
    assert events, "expected a billing.low_balance event"
    autopay_calls = [c for c in app.state.stripe_calls[before:] if c["path"] == "/payment_intents"]
    assert len(autopay_calls) == 1
    assert autopay_calls[0]["data"]["metadata[caspian]"] == "autopay"
    # Best practice: the off-session charge carries an idempotency key so a retry
    # can never double-bill the card.
    assert autopay_calls[0]["idempotency_key"], "autopay charge must be idempotent"

    # autopay settlement webhook credits the ledger
    settle = {"type": "payment_intent.succeeded",
              "data": {"object": {"id": "pi_auto_settle", "amount": 2000,
                                  "metadata": {"caspian": "autopay", "project_id": project_id}}}}
    payload, headers = _signed(settle)
    TestClient(app).post("/internal/billing/stripe/webhooks", content=payload, headers=headers)
    assert client.get("/v1/billing").json()["credit_cents"] == 2050


def test_monthly_cap_blocks_and_emits_event(client, app):
    project_id = _project_id(app)
    with app.state.session_factory() as s:
        credit_topup(s, project_id, "grant", "grant:cap", 5000)
    client.put("/v1/billing/limits", json={"monthly_cap_cents": 1})

    # accrue >= 1 cent of whatsapp spend: outbound costs 0.5c each
    client.post("/v1/connections/whatsapp", json={})
    from comm_gateway.jobs import run_pending_jobs

    run_pending_jobs(app.state.session_factory, app.state.providers)
    provider = app.state.providers["fake-whatsapp"]
    anon = TestClient(app)
    for i in range(3):
        anon.post(
            "/internal/providers/fake-whatsapp/webhooks",
            content=provider.webhook_payload(from_number="+15551112222", text=f"hi {i}"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    run_pending_jobs(app.state.session_factory, app.state.providers)
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[-1]["data"]["message"]["id"]
    replies = 0
    r = None
    for _ in range(4):
        r = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "ok"})
        if r.status_code == 429:
            break
        replies += 1
        run_pending_jobs(app.state.session_factory, app.state.providers)
    assert r.status_code == 429
    assert r.json()["detail"]["reason"] == "monthly_cap_reached"
    limit_events = client.get("/v1/events", params={"type": "billing.limit_reached"}).json()
    assert len(limit_events) == 1


def test_webhook_fails_closed_when_secret_unset():
    """A billing webhook must NEVER process an unverified event: with no signing
    secret configured, refuse (503) instead of crediting."""
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False,
        stripe_secret_key="sk_test_x", stripe_webhook_secret="",
    )
    app = create_app(settings, providers={"fake": FakeEmailProvider()})
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = client.post(
        "/internal/billing/stripe/webhooks",
        content=json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}),
    )
    assert r.status_code == 503
