"""WhatsApp, RCS, and iMessage channels — one connect + send + inbound each."""

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_channels import (
    FakeCaspianPhoneIMessageProvider,
    FakeRcsProvider,
    FakeWhatsAppProvider,
)
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"

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



def _app(provider):
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    app = create_app(settings, providers={provider.name: provider})
    _grant_credit(app)
    return app, TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def _run(app):
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def _connect(client, app, channel):
    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Agent"}).json()
    conn = client.post(
        f"/v1/connections/{channel}",
        json={"customer_id": cust["id"], "agent_id": agent["id"]},
    ).json()
    _run(app)
    return client.get(f"/v1/connections/{conn['id']}").json()


def test_whatsapp_connect_and_reply():
    provider = FakeWhatsAppProvider()
    app, client = _app(provider)
    conn = _connect(client, app, "whatsapp")
    assert conn["channel"] == "whatsapp"

    client.post(
        "/internal/providers/fake-whatsapp/webhooks",
        content=provider.webhook_payload(from_number="+919513843202", text="hi"),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    _run(app)
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    msg = events[-1]["data"]["message"]
    assert msg["channel"] == "whatsapp"
    client.post(f"/v1/messages/{msg['id']}/reply", json={"text": "hello back"})
    _run(app)
    assert provider.sent[-1]["to"] == "+919513843202"
    assert provider.sent[-1]["text"] == "hello back"


def test_whatsapp_has_no_initiate():
    # WhatsApp business-initiated needs templates -> INITIATE deliberately absent.
    provider = FakeWhatsAppProvider()
    app, client = _app(provider)
    caps = {c["channel"]: c for c in client.get("/v1/channels").json()}
    assert "initiate" not in caps["whatsapp"]["capabilities"]
    assert "send" in caps["whatsapp"]["capabilities"]


def test_rcs_connect_and_send():
    provider = FakeRcsProvider()
    app, client = _app(provider)
    conn = _connect(client, app, "rcs")
    assert conn["channel"] == "rcs"

    client.post(
        "/internal/providers/fake-rcs/webhooks",
        content=provider.webhook_payload(from_number="+15551230000", text="ping"),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    _run(app)
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    msg = events[-1]["data"]["message"]
    assert msg["channel"] == "rcs"


def test_imessage_connect_and_otp():
    provider = FakeCaspianPhoneIMessageProvider()
    app, client = _app(provider)
    conn = _connect(client, app, "imessage")
    assert conn["channel"] == "imessage"
    assert "otp" in client.get("/v1/channels").json()[0]["capabilities"]

    client.post(
        "/internal/providers/fake-caspian-imessage/webhooks",
        json=provider.webhook_payload(
            to_number=conn["address"], from_number="Apple", text="Your Apple ID code is 449102"
        ),
    )
    _run(app)
    otp = client.get("/v1/events", params={"type": "message.otp"}).json()
    assert otp[-1]["data"]["code"] == "449102"


def test_meta_whatsapp_inbound_parse():
    # Meta Cloud API webhook shape -> normalized message (no gateway/creds needed).
    import json  # noqa: PLC0415

    from comm_gateway.providers.meta_whatsapp import parse_meta_webhook  # noqa: PLC0415

    payload = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "PN123"},
                                "messages": [
                                    {
                                        "from": "919513843202",
                                        "id": "wamid.ABC",
                                        "type": "text",
                                        "text": {"body": "hi from real whatsapp"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()
    msgs = parse_meta_webhook(payload, "PN123")
    assert len(msgs) == 1
    assert msgs[0].sender_address == "919513843202"
    assert msgs[0].text == "hi from real whatsapp"
    assert msgs[0].chat_type == "whatsapp"
    assert msgs[0].provider_inbox_id == "PN123"
