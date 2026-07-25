"""Phone channel: real-mobile (modem) OTP path + Telnyx business-SMS path."""

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_modem import FakeModemProvider
from comm_gateway.providers.fakes.fake_phone import FakePhoneProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _app(provider):
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    app = create_app(settings, providers={provider.name: provider})
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    return app, client


def _run(app):
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def _connect(client, app, capabilities=None):
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Verifier"}).json()
    body = {"customer_id": customer["id"], "agent_id": agent["id"]}
    if capabilities is not None:
        body["capabilities"] = capabilities
    conn = client.post("/v1/connections/phone", json=body).json()
    _run(app)
    return client.get(f"/v1/connections/{conn['id']}").json()


def test_phone_channel_reports_otp_capability():
    app, client = _app(FakeModemProvider())
    caps = {c["channel"]: c for c in client.get("/v1/channels").json()}
    assert "otp" in caps["phone"]["capabilities"]
    assert "initiate" in caps["phone"]["capabilities"]


def test_provision_real_mobile_number():
    app, client = _app(FakeModemProvider())
    conn = _connect(client, app)
    assert conn["status"] == "active"
    assert conn["channel"] == "phone"
    assert conn["address"].startswith("+1")


def test_inbound_otp_emits_message_otp_event():
    provider = FakeModemProvider()
    app, client = _app(provider)
    conn = _connect(client, app)  # default manifest -> full ceiling incl. otp

    payload = provider.webhook_payload(
        from_number="+18888888888", text="Your verification code is 774531"
    )
    assert client.post("/internal/providers/fake-modem/webhooks", json=payload).status_code == 204
    _run(app)

    received = client.get("/v1/events", params={"type": "message.received"}).json()
    assert received[-1]["data"]["message"]["text"] == "Your verification code is 774531"

    otp = client.get("/v1/events", params={"type": "message.otp"}).json()
    assert len(otp) == 1
    assert otp[0]["data"]["code"] == "774531"
    assert otp[0]["data"]["connection_id"] == conn["id"]


def test_non_otp_text_emits_no_otp_event():
    provider = FakeModemProvider()
    app, client = _app(provider)
    _connect(client, app)
    client.post(
        "/internal/providers/fake-modem/webhooks",
        json=provider.webhook_payload(text="hey, running 5 min late"),
    )
    _run(app)
    assert client.get("/v1/events", params={"type": "message.otp"}).json() == []


def test_otp_withheld_by_manifest_suppresses_extraction():
    provider = FakeModemProvider()
    app, client = _app(provider)
    # Cautious agent takes messaging but NOT otp.
    _connect(client, app, capabilities=["receive", "send"])
    client.post(
        "/internal/providers/fake-modem/webhooks",
        json=provider.webhook_payload(text="Your code is 111222"),
    )
    _run(app)
    # Message still received, but no code is extracted without the grant.
    assert len(client.get("/v1/events", params={"type": "message.received"}).json()) == 1
    assert client.get("/v1/events", params={"type": "message.otp"}).json() == []


def test_reply_routes_to_sender_number():
    provider = FakeModemProvider()
    app, client = _app(provider)
    _connect(client, app)
    client.post(
        "/internal/providers/fake-modem/webhooks",
        json=provider.webhook_payload(from_number="+17775551234", text="ping"),
    )
    _run(app)
    inbound_id = client.get("/v1/events", params={"type": "message.received"}).json()[-1][
        "data"
    ]["message"]["id"]

    client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "pong"})
    _run(app)
    assert provider.sent[-1]["to"] == "+17775551234"
    assert provider.sent[-1]["text"] == "pong"


def test_cpaas_phone_offers_best_effort_otp():
    # The CPaaS (Telnyx) route also carries otp — best-effort: codes from the
    # long tail of services that don't line-type-filter still arrive.
    provider = FakePhoneProvider()
    app, client = _app(provider)
    caps = {c["channel"]: c for c in client.get("/v1/channels").json()}
    assert "otp" in caps["phone"]["capabilities"]
    assert "send" in caps["phone"]["capabilities"]


def test_cpaas_route_extracts_otp():
    # Prove the CPaaS backend runs the same extraction pipeline as the real SIM.
    provider = FakePhoneProvider()
    app, client = _app(provider)
    customer = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Verifier"}).json()
    conn = client.post(
        "/v1/connections/phone",
        json={"customer_id": customer["id"], "agent_id": agent["id"]},
    ).json()
    _run(app)
    conn = client.get(f"/v1/connections/{conn['id']}").json()

    client.post(
        "/internal/providers/fake-phone/webhooks",
        json=provider.webhook_payload(
            to_number=conn["address"], from_number="+18005550100", text="Your code is 662019"
        ),
    )
    _run(app)
    otp = client.get("/v1/events", params={"type": "message.otp"}).json()
    assert otp[-1]["data"]["code"] == "662019"
    assert otp[-1]["data"]["connection_id"] == conn["id"]


def test_phone_initiate_cold_starts_sms():
    # INITIATE is declared on phone providers; prove it actually sends (the method
    # was previously missing despite the capability being advertised).
    provider = FakePhoneProvider()
    app, client = _app(provider)
    conn = _connect(client, app)
    r = client.post(
        f"/v1/connections/{conn['id']}/initiate",
        json={"recipient": "+15559998888", "text": "hi"},
    )
    assert r.status_code == 202
    _run(app)
    assert provider.sent[-1]["to"] == "+15559998888"
    assert provider.sent[-1]["text"] == "hi"
    sent = client.get("/v1/events", params={"type": "message.sent"}).json()
    assert len(sent) == 1


def test_each_connection_commissions_its_own_number():
    provider = FakePhoneProvider()  # per-agent commissioning mode
    app, client = _app(provider)
    numbers = []
    for name in ("Agent A", "Agent B"):
        cust = client.post("/v1/customers", json={"name": name}).json()
        agent = client.post("/v1/agents", json={"name": name}).json()
        conn = client.post(
            "/v1/connections/phone",
            json={"customer_id": cust["id"], "agent_id": agent["id"]},
        ).json()
        _run(app)
        numbers.append(client.get(f"/v1/connections/{conn['id']}").json()["address"])
    # Two agents -> two distinct provisioned numbers.
    assert numbers[0] != numbers[1]
    assert provider.provisioned == numbers


def test_release_deprovisions_the_number():
    provider = FakePhoneProvider()
    app, client = _app(provider)
    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Agent"}).json()
    conn = client.post(
        "/v1/connections/phone",
        json={"customer_id": cust["id"], "agent_id": agent["id"]},
    ).json()
    _run(app)
    number = client.get(f"/v1/connections/{conn['id']}").json()["address"]

    r = client.delete(f"/v1/connections/{conn['id']}")
    assert r.status_code == 202
    _run(app)
    assert provider.released == [number]
    assert client.get(f"/v1/connections/{conn['id']}").json()["status"] == "released"


def test_inbound_routes_to_the_matching_commissioned_number():
    provider = FakePhoneProvider()
    app, client = _app(provider)
    # Two agents, two numbers; inbound to agent B's number must land on B.
    conns = []
    for name in ("A", "B"):
        cust = client.post("/v1/customers", json={"name": name}).json()
        agent = client.post("/v1/agents", json={"name": name}).json()
        conn = client.post(
            "/v1/connections/phone",
            json={"customer_id": cust["id"], "agent_id": agent["id"]},
        ).json()
        _run(app)
        conns.append(client.get(f"/v1/connections/{conn['id']}").json())

    client.post(
        "/internal/providers/fake-phone/webhooks",
        json=provider.webhook_payload(to_number=conns[1]["address"], text="ping B"),
    )
    _run(app)
    msg = client.get("/v1/events", params={"type": "message.received"}).json()[-1]["data"]
    assert msg["connection_id"] == conns[1]["id"]
