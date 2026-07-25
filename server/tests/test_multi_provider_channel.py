"""Two providers can serve one channel (e.g. whatsapp via twilio + meta).

connect picks which provider backs the connection; inbound routes to the
provider-specific connection by the webhook URL; the registry allows it.
"""

from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_channels import FakeWhatsAppProvider
from fastapi.testclient import TestClient

API_KEY = "comm_multi_test"

def _grant_credit(app, api_key=API_KEY):
    """Paid channels are credit-gated now; tests fund the bootstrap project."""
    from comm_gateway.auth import hash_key
    from comm_gateway.billing import credit_topup
    from comm_gateway.models import ApiKey
    from sqlalchemy import select

    with app.state.session_factory() as s:
        project_id = s.execute(
            select(ApiKey.project_id).where(ApiKey.key_hash == hash_key(api_key))
        ).scalar_one()
        credit_topup(s, project_id, "grant", f"grant:{project_id}", 100_000)



class SecondWhatsApp(FakeWhatsAppProvider):
    # a distinct provider name on the SAME channel, standing in for meta-whatsapp
    name = "meta-fake-whatsapp"
    channel = "whatsapp"


def _app():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False)
    twilio = FakeWhatsAppProvider(from_number="+1111")   # first inserted -> default
    meta = SecondWhatsApp(from_number="+2222")
    app = create_app(settings, providers={twilio.name: twilio, meta.name: meta})
    _grant_credit(app)
    return app, twilio, meta


def _client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def _active(app, client, conn):
    run_pending_jobs(app.state.session_factory, app.state.providers)
    return client.get(f"/v1/connections/{conn['id']}").json()


def test_build_providers_allows_two_providers_per_channel():
    from comm_gateway.providers import build_providers

    settings = Settings(
        providers="twilio-whatsapp,meta-whatsapp",
        twilio_account_sid="AC", twilio_auth_token="tok", twilio_whatsapp_from="+1",
        meta_wa_phone_number_id="PN", meta_wa_access_token="tok", meta_wa_app_secret="s",
    )
    providers = build_providers(settings)
    assert set(providers) == {"twilio-whatsapp", "meta-whatsapp"}
    assert providers["twilio-whatsapp"].channel == "whatsapp"
    assert providers["meta-whatsapp"].channel == "whatsapp"


def test_connect_defaults_to_first_provider():
    app, twilio, meta = _app()
    c = _client(app)
    conn = c.post("/v1/connections/whatsapp", json={}).json()
    conn = _active(app, c, conn)
    assert conn["address"] == "+1111"  # twilio (first configured)


def test_connect_explicit_provider_selects_it():
    app, twilio, meta = _app()
    c = _client(app)
    conn = c.post("/v1/connections/whatsapp", json={"provider": "meta-fake-whatsapp"}).json()
    conn = _active(app, c, conn)
    assert conn["address"] == "+2222"  # the explicitly requested provider


def test_connect_unknown_provider_is_422():
    app, twilio, meta = _app()
    c = _client(app)
    r = c.post("/v1/connections/whatsapp", json={"provider": "nope"})
    assert r.status_code == 422
    assert "not configured" in r.json()["detail"]


def test_both_providers_coexist_for_same_scope():
    app, twilio, meta = _app()
    c = _client(app)
    t = c.post("/v1/connections/whatsapp", json={"provider": "fake-whatsapp"}).json()
    m = c.post("/v1/connections/whatsapp", json={"provider": "meta-fake-whatsapp"}).json()
    assert t["id"] != m["id"]  # not deduped into one — different providers
    assert _active(app, c, t)["address"] == "+1111"
    assert _active(app, c, m)["address"] == "+2222"


def test_second_connection_on_same_number_fails_cleanly():
    # A shared number (the Twilio sandbox) backs only one connection: a second
    # claimant fails instead of crashing inbound with an ambiguous lookup.
    app, twilio, meta = _app()
    c = _client(app)
    # two distinct scopes so connect isn't deduped into one connection
    cust_a = c.post("/v1/customers", json={"name": "A"}).json()
    agt_a = c.post("/v1/agents", json={"name": "A"}).json()
    cust_b = c.post("/v1/customers", json={"name": "B"}).json()
    agt_b = c.post("/v1/agents", json={"name": "B"}).json()

    a = c.post("/v1/connections/whatsapp",
               json={"customer_id": cust_a["id"], "agent_id": agt_a["id"]}).json()
    b = c.post("/v1/connections/whatsapp",
               json={"customer_id": cust_b["id"], "agent_id": agt_b["id"]}).json()
    run_pending_jobs(app.state.session_factory, app.state.providers)

    a = c.get(f"/v1/connections/{a['id']}").json()
    b = c.get(f"/v1/connections/{b['id']}").json()
    # first wins, second fails with a clear error (both wanted +1111)
    statuses = {a["status"], b["status"]}
    assert statuses == {"active", "failed"}
    failed = a if a["status"] == "failed" else b
    assert "already connected" in (failed["error"] or "")

    # inbound to the shared number resolves to exactly the one active connection
    c.post("/internal/providers/fake-whatsapp/webhooks",
           content=twilio.webhook_payload(text="who gets this"))
    run_pending_jobs(app.state.session_factory, app.state.providers)
    events = c.get("/v1/events", params={"type": "message.received"}).json()
    active_id = a["id"] if a["status"] == "active" else b["id"]
    assert events[-1]["data"]["connection_id"] == active_id


def test_inbound_routes_to_the_provider_specific_connection():
    app, twilio, meta = _app()
    c = _client(app)
    t = c.post("/v1/connections/whatsapp", json={"provider": "fake-whatsapp"}).json()
    m = c.post("/v1/connections/whatsapp", json={"provider": "meta-fake-whatsapp"}).json()
    run_pending_jobs(app.state.session_factory, app.state.providers)

    # inbound to meta's number goes to the meta connection, not twilio's
    c.post("/internal/providers/meta-fake-whatsapp/webhooks",
           content=meta.webhook_payload(text="hello meta"))
    run_pending_jobs(app.state.session_factory, app.state.providers)
    events = c.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hello meta"
    assert events[-1]["data"]["connection_id"] == m["id"]

    # and inbound to twilio's number goes to the twilio connection
    c.post("/internal/providers/fake-whatsapp/webhooks",
           content=twilio.webhook_payload(text="hello twilio"))
    run_pending_jobs(app.state.session_factory, app.state.providers)
    events = c.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hello twilio"
    assert events[-1]["data"]["connection_id"] == t["id"]
