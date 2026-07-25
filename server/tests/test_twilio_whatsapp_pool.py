"""Option 1a: Caspian-provisioned WhatsApp numbers via Twilio.

The twilio-whatsapp provider hands each agent its own number from a pool of
Caspian-owned Twilio WhatsApp senders, falling back to a single shared number
when no pool is configured. These tests cover the provider's multi-sender
behaviour directly and the pool-allocation path through the connect API.
"""

from urllib.parse import parse_qs

import httpx
import pytest
from comm_gateway import crypto
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.models import Connection
from comm_gateway.providers.base import OutboundMessage, ProvisionRequest
from comm_gateway.providers.twilio_whatsapp import TwilioWhatsAppProvider
from cryptography.fernet import Fernet
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

SHARED = "+14155238886"
POOL_A = "+14150000001"
POOL_B = "+14150000002"


def _capturing_provider(pool: str = "", from_number: str = SHARED):
    """A provider whose Twilio Messages API is a mock that records each From."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        sent.append(form)
        return httpx.Response(201, json={"sid": "SM_test"})

    provider = TwilioWhatsAppProvider(
        account_sid="AC_test",
        auth_token="tok_test",
        from_number=from_number,
        pool=pool,
    )
    provider._client = httpx.Client(
        base_url="https://api.twilio.com",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    return provider, sent


# --- provider: multi-sender outbound -----------------------------------------

def test_pool_numbers_parses_comma_separated_setting():
    provider = TwilioWhatsAppProvider(
        account_sid="AC", auth_token="tok",
        pool=f" {POOL_A}, {POOL_B} ,",  # whitespace + trailing comma tolerated
    )
    assert provider.pool_numbers == [POOL_A, POOL_B]


def test_pool_only_deployment_needs_no_shared_number():
    # A pool alone satisfies the "at least one sender" requirement.
    provider = TwilioWhatsAppProvider(account_sid="AC", auth_token="tok", pool=POOL_A)
    assert provider.pool_numbers == [POOL_A]


def test_missing_all_senders_raises():
    with pytest.raises(ValueError):
        TwilioWhatsAppProvider(account_sid="AC", auth_token="tok")


def test_send_uses_assigned_number_when_set():
    provider, sent = _capturing_provider(pool=f"{POOL_A},{POOL_B}")
    provider.send(
        "ignored-inbox-id",
        OutboundMessage(text="hi", to=("+15551230000",)),
        credentials={"from_number": POOL_A},
    )
    assert sent[-1]["From"] == f"whatsapp:{POOL_A}"
    assert sent[-1]["To"] == "whatsapp:+15551230000"


def test_send_falls_back_to_shared_number_without_credentials():
    provider, sent = _capturing_provider()
    provider.send("ignored", OutboundMessage(text="hi", to=("+15551230000",)))
    assert sent[-1]["From"] == f"whatsapp:{SHARED}"


def test_reply_uses_assigned_number():
    provider, sent = _capturing_provider(pool=POOL_B)
    provider.reply(
        "ignored",
        "+15559998888:SM_prev",
        OutboundMessage(text="re"),
        credentials={"from_number": POOL_B},
    )
    assert sent[-1]["From"] == f"whatsapp:{POOL_B}"
    assert sent[-1]["To"] == "whatsapp:+15559998888"


def test_provision_returns_assigned_number():
    provider, _ = _capturing_provider(pool=POOL_A)
    result = provider.provision(
        ProvisionRequest(
            connection_id="conn_1", customer_id="cus_1", agent_id="agt_1",
            credentials={"from_number": POOL_A},
        )
    )
    assert result.address == POOL_A
    assert result.provider_resource_id == POOL_A


def test_provision_without_pool_returns_shared_number():
    provider, _ = _capturing_provider()
    result = provider.provision(
        ProvisionRequest(connection_id="c", customer_id="cu", agent_id="a")
    )
    assert result.address == SHARED
    assert result.provider_resource_id == SHARED


# --- pool allocation through the connect API ---------------------------------

def _make_app(pool: str = "", from_number: str = SHARED):
    settings = Settings(
        database_url="sqlite://",
        providers="twilio-whatsapp",
        bootstrap_api_key=API_KEY,
        inline_worker=False,
        credentials_key=Fernet.generate_key().decode(),
        twilio_account_sid="AC_test",
        twilio_auth_token="tok_test",
        twilio_whatsapp_from=from_number,
        twilio_whatsapp_pool=pool,
    )
    application = create_app(settings)
    _grant_credit(application)
    return application


@pytest.fixture()
def pool_app():
    app = _make_app(pool=f"{POOL_A},{POOL_B}")
    yield app
    crypto.configure_cipher("")


@pytest.fixture()
def shared_app():
    app = _make_app(pool="")
    yield app
    crypto.configure_cipher("")


def _run_jobs(app):
    from comm_gateway.jobs import run_pending_jobs

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def _connect(client, app, name):
    cust = client.post("/v1/customers", json={"name": f"cust-{name}"}).json()
    agent = client.post("/v1/agents", json={"name": f"agent-{name}"}).json()
    r = client.post("/v1/connections/whatsapp", json={
        "customer_id": cust["id"], "agent_id": agent["id"], "display_name": name,
    })
    assert r.status_code == 201, r.text
    _run_jobs(app)
    return r.json()


def test_pool_gives_two_agents_different_numbers(pool_app):
    app = pool_app
    c = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    first = _connect(c, app, "one")
    second = _connect(c, app, "two")

    a = c.get(f"/v1/connections/{first['id']}").json()
    b = c.get(f"/v1/connections/{second['id']}").json()
    assert a["status"] == "active" and b["status"] == "active"
    assert a["address"] in {POOL_A, POOL_B}
    assert b["address"] in {POOL_A, POOL_B}
    assert a["address"] != b["address"]

    with app.state.session_factory() as s:
        for conn_id in (first["id"], second["id"]):
            row = s.query(Connection).filter(Connection.id == conn_id).one()
            assert row.provider_resource_id == crypto.read_credentials(row)["from_number"]


def test_pool_exhaustion_returns_409(pool_app):
    app = pool_app
    c = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    _connect(c, app, "one")
    _connect(c, app, "two")  # both pool numbers now taken

    cust = c.post("/v1/customers", json={"name": "cust-three"}).json()
    agent = c.post("/v1/agents", json={"name": "agent-three"}).json()
    r = c.post("/v1/connections/whatsapp", json={
        "customer_id": cust["id"], "agent_id": agent["id"], "display_name": "three",
    })
    assert r.status_code == 409, r.text


def test_no_pool_uses_single_shared_number(shared_app):
    app = shared_app
    c = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    first = _connect(c, app, "one")
    second = _connect(c, app, "two")

    a = c.get(f"/v1/connections/{first['id']}").json()
    b = c.get(f"/v1/connections/{second['id']}").json()
    # Regression: without a pool everyone shares the one configured sender.
    assert a["address"] == SHARED
    assert b["address"] == SHARED

    with app.state.session_factory() as s:
        row = s.query(Connection).filter(Connection.id == first["id"]).one()
        # No per-connection number is pinned in the shared-number path.
        assert "from_number" not in crypto.read_credentials(row)
