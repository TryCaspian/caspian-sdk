"""caspian-phone provider - SIM-backed number commissioning + OTP receive (via fake)."""

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.caspian_phone import parse_message_event
from comm_gateway.providers.fakes.fake_caspian_phone import FakeCaspianPhoneProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


def _app():
    provider = FakeCaspianPhoneProvider()
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    app = create_app(settings, providers={provider.name: provider})
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    return app, client, provider


def _run(app):
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def _connect(client, app):
    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Signup Agent"}).json()
    conn = client.post(
        "/v1/connections/phone", json={"customer_id": cust["id"], "agent_id": agent["id"]}
    ).json()
    _run(app)
    return client.get(f"/v1/connections/{conn['id']}").json()


def test_caspian_phone_commissions_a_real_number_with_otp():
    app, client, provider = _app()
    caps = {c["provider"]: c for c in client.get("/v1/channels").json()}
    assert "otp" in caps["fake-caspian-phone"]["capabilities"]

    conn = _connect(client, app)
    assert conn["status"] == "active"
    assert conn["address"] in provider.provisioned


def test_caspian_phone_receives_and_extracts_otp():
    app, client, provider = _app()
    conn = _connect(client, app)
    client.post(
        "/internal/providers/fake-caspian-phone/webhooks",
        json=provider.webhook_payload(
            to_number=conn["address"],
            from_number="Instagram",
            text="Your Instagram code is 553201. Don't share it.",
        ),
    )
    _run(app)
    otp = client.get("/v1/events", params={"type": "message.otp"}).json()
    assert otp[-1]["data"]["code"] == "553201"


def test_caspian_phone_release_hands_number_back():
    app, client, provider = _app()
    conn = _connect(client, app)
    number = conn["address"]
    client.delete(f"/v1/connections/{conn['id']}")
    _run(app)
    assert provider.released == [number]


def test_parse_message_event_skips_payload_missing_data_or_from_or_to():
    """A malformed inbound payload (missing "data", or missing "from"/"to"
    inside it) must be skipped, not raise a KeyError."""
    assert parse_message_event({"event": "agent.message"}) == []
    assert parse_message_event(
        {"event": "agent.message", "data": {"direction": "inbound", "to": "+1555"}}
    ) == []
    assert parse_message_event(
        {"event": "agent.message", "data": {"direction": "inbound", "from": "+1555"}}
    ) == []


def test_parse_message_event_accepts_well_formed_payload():
    msgs = parse_message_event(
        {
            "event": "agent.message",
            "channel": "sms",
            "data": {
                "direction": "inbound",
                "from": "+15551234567",
                "to": "+15559990000",
                "message": "hi",
                "numberId": "num_1",
                "receivedAt": "2026-01-01T00:00:00Z",
            },
        }
    )
    assert len(msgs) == 1
    assert msgs[0].sender_address == "+15551234567"
    assert msgs[0].provider_inbox_id == "+15559990000"
