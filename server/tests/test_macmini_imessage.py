"""Self-hosted iMessage via BlueBubbles on a Mac mini — unit + registry wiring.

Mocks the BlueBubbles REST server with httpx.MockTransport (no live Mac mini).
"""

import json

import httpx
import pytest
from comm_gateway.config import Settings
from comm_gateway.providers.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from comm_gateway.providers.macmini_imessage import (
    MacMiniIMessageProvider,
    chat_guid_for,
)
from comm_gateway.providers.registry import _build_one

BASE_URL = "http://macmini.local:1234"
PASSWORD = "s3cret-pw"
HANDLE = "+15550001111"


def _handler(calls: list) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/message/text"
        # BlueBubbles auth rides as a query param on every request.
        assert request.url.params.get("password") == PASSWORD
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200, json={"status": 200, "data": {"guid": "p:0/ABC-123", "text": body["message"]}}
        )

    return httpx.MockTransport(handle)


def _provider(calls: list, webhook_secret: str = "") -> MacMiniIMessageProvider:
    p = MacMiniIMessageProvider(
        base_url=BASE_URL, password=PASSWORD, handle=HANDLE, webhook_secret=webhook_secret
    )
    # Rebuild the client onto the mock transport, keeping the password param.
    p._client = httpx.Client(
        base_url=BASE_URL, params={"password": PASSWORD},
        transport=_handler(calls), timeout=5.0,
    )
    return p


# --- helpers -----------------------------------------------------------------

def test_chat_guid_for_phone_email_and_passthrough():
    assert chat_guid_for("+15551234567") == "iMessage;-;+15551234567"
    assert chat_guid_for("a@example.com") == "iMessage;-;a@example.com"
    # already-built guids pass through unchanged (reply/initiate hand either form)
    assert chat_guid_for("iMessage;-;+15551234567") == "iMessage;-;+15551234567"


# --- construction ------------------------------------------------------------

def test_missing_required_config_raises():
    with pytest.raises(ValueError):
        MacMiniIMessageProvider(base_url="", password=PASSWORD, handle=HANDLE)
    with pytest.raises(ValueError):
        MacMiniIMessageProvider(base_url=BASE_URL, password="", handle=HANDLE)
    with pytest.raises(ValueError):
        MacMiniIMessageProvider(base_url=BASE_URL, password=PASSWORD, handle="")


def test_provision_returns_configured_handle():
    p = _provider([])
    res = p.provision(ProvisionRequest("c", "cu", "ag"))
    assert res.address == HANDLE
    assert res.provider_resource_id == HANDLE


# --- outbound ----------------------------------------------------------------

def test_send_builds_chat_guid_and_returns_composite_id():
    calls: list = []
    p = _provider(calls)
    res = p.send("inbox", OutboundMessage(text="hi there", to=("+15559998888",)))
    assert calls[0]["chatGuid"] == "iMessage;-;+15559998888"
    assert calls[0]["message"] == "hi there"
    assert calls[0]["method"] == "apple-script"
    assert "tempGuid" in calls[0]
    assert res.provider_message_id == "iMessage;-;+15559998888:p:0/ABC-123"
    assert res.provider_thread_id == "iMessage;-;+15559998888"


def test_reply_routes_into_the_same_chat():
    calls: list = []
    p = _provider(calls)
    # composite id from a prior inbound; the guid itself contains a ':'
    res = p.reply("inbox", "iMessage;-;+15559998888:p:0/ORIG-9", OutboundMessage(text="re"))
    assert calls[0]["chatGuid"] == "iMessage;-;+15559998888"
    assert calls[0]["message"] == "re"
    assert res.provider_thread_id == "iMessage;-;+15559998888"


def test_initiate_cold_starts_to_a_handle():
    calls: list = []
    p = _provider(calls)
    res = p.initiate("inbox", "cold@example.com", OutboundMessage(text="first contact"))
    assert calls[0]["chatGuid"] == "iMessage;-;cold@example.com"
    assert res.provider_message_id == "iMessage;-;cold@example.com:p:0/ABC-123"


# --- inbound webhook ---------------------------------------------------------

def _inbound_payload(is_from_me: bool = False) -> bytes:
    return json.dumps(
        {
            "type": "new-message",
            "data": {
                "guid": "in-guid-1",
                "text": "hello agent",
                "isFromMe": is_from_me,
                "handle": {"address": "+15557776666"},
                "chats": [{"guid": "iMessage;-;+15557776666"}],
            },
        }
    ).encode()


def test_parse_webhook_new_message_yields_one_inbound():
    p = _provider([])
    msgs = p.parse_webhook(_inbound_payload(), {})
    assert len(msgs) == 1
    m = msgs[0]
    assert m.text == "hello agent"
    assert m.sender_address == "+15557776666"
    assert m.provider_inbox_id == HANDLE  # routes to this deployment's connection
    assert m.provider_thread_id == "iMessage;-;+15557776666"
    assert m.provider_message_id == "iMessage;-;+15557776666:in-guid-1"
    assert m.external_event_id == "in-guid-1"
    assert m.chat_type == "imessage"


def test_parse_webhook_ignores_our_own_echoes():
    p = _provider([])
    assert p.parse_webhook(_inbound_payload(is_from_me=True), {}) == []


def test_parse_webhook_ignores_non_new_message():
    p = _provider([])
    other = json.dumps({"type": "typing-indicator", "data": {}}).encode()
    assert p.parse_webhook(other, {}) == []


# --- opt-in webhook secret ---------------------------------------------------

def test_secret_enforced_only_when_configured():
    # No secret configured: any (or no) header parses fine.
    p = _provider([])
    assert len(p.parse_webhook(_inbound_payload(), {})) == 1


def test_bad_or_missing_secret_rejected():
    p = _provider([], webhook_secret="hunter2")
    with pytest.raises(WebhookVerificationError):
        p.parse_webhook(_inbound_payload(), {})  # missing header
    with pytest.raises(WebhookVerificationError):
        p.parse_webhook(_inbound_payload(), {"x-bluebubbles-secret": "wrong"})


def test_good_secret_parses():
    p = _provider([], webhook_secret="hunter2")
    msgs = p.parse_webhook(_inbound_payload(), {"X-BlueBubbles-Secret": "hunter2"})
    assert len(msgs) == 1


# --- registry wiring ---------------------------------------------------------

def test_registry_builds_provider_from_settings():
    settings = Settings(
        database_url="sqlite://",
        macmini_bluebubbles_url=BASE_URL,
        macmini_bluebubbles_password=PASSWORD,
        macmini_imessage_handle=HANDLE,
    )
    provider = _build_one("macmini-imessage", settings)
    assert provider.name == "macmini-imessage"
    assert provider.channel == "imessage"
    assert {"receive", "reply", "send", "initiate"} == set(provider.capabilities)


def test_registry_missing_config_raises():
    settings = Settings(database_url="sqlite://")  # nothing set
    with pytest.raises(ValueError):
        _build_one("macmini-imessage", settings)


# --- through the connect API (no per-connection credentials) -----------------

def test_connect_imessage_slots_in_via_the_generic_endpoint():
    from comm_gateway import crypto
    from comm_gateway.jobs import run_pending_jobs
    from comm_gateway.main import create_app
    from fastapi.testclient import TestClient

    api_key = "comm_test_key"
    settings = Settings(
        database_url="sqlite://",
        providers="macmini-imessage",
        bootstrap_api_key=api_key,
        inline_worker=False,
        macmini_bluebubbles_url=BASE_URL,
        macmini_bluebubbles_password=PASSWORD,
        macmini_imessage_handle=HANDLE,
    )
    app = create_app(settings)
    # macmini-imessage runs on Caspian's paid network -> connecting needs credit.
    from comm_gateway.auth import hash_key
    from comm_gateway.crypto import _encrypt
    from comm_gateway.models import ApiKey, DashboardAccount
    from sqlalchemy import select

    with app.state.session_factory() as s:
        pid = s.execute(
            select(ApiKey.project_id).where(ApiKey.key_hash == hash_key(api_key))
        ).scalar_one()
        s.add(DashboardAccount(email="im-dev@example.com", project_id=pid,
                               api_key_enc=_encrypt({"api_key": api_key}),
                               credit_cents=10000))
        s.commit()
    client = TestClient(app, headers={"Authorization": f"Bearer {api_key}"})
    try:
        # imessage needs no per-connection credentials (like email): the same
        # /v1/connections/imessage endpoint serves this provider.
        conn = client.post("/v1/connections/imessage", json={}).json()
        assert run_pending_jobs(app.state.session_factory, app.state.providers) >= 1
        active = client.get(f"/v1/connections/{conn['id']}").json()
        assert active["status"] == "active"
        assert active["address"] == HANDLE

        caps = {c["provider"]: c for c in client.get("/v1/channels").json()}
        assert set(caps["macmini-imessage"]["capabilities"]) == {
            "receive", "reply", "send", "initiate"
        }
    finally:
        crypto.configure_cipher("")
