"""Voice channel (Twilio Voice + ConversationRelay): place + receive calls.

The provider is the telephony/orchestration layer only — it provisions a
voice-capable number, places outbound calls that bridge to an external realtime
AI endpoint, and normalizes inbound-call webhooks. The realtime speech loop is
NOT exercised here (it lives outside the gateway).
"""

import base64
import hashlib
import hmac
from urllib.parse import urlencode

import httpx
import pytest
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from comm_gateway.providers.voice import VoiceProvider, connectrelay_twiml
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"
RELAY_URL = "wss://relay.example.com/agent"
CALL_SID = "CA1234567890abcdef1234567890abcdef"


def _provider(handler=None, **over):
    kwargs = dict(
        account_sid="AC_test",
        auth_token="tok_test",
        from_number="+15550001111",
        conversationrelay_url=RELAY_URL,
    )
    kwargs.update(over)
    provider = VoiceProvider(**kwargs)
    if handler is not None:
        provider._client = httpx.Client(
            base_url="https://api.twilio.com",
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )
    return provider


def _calls_handler(record: list):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Calls.json"):
            record.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(201, json={"sid": CALL_SID})
        return httpx.Response(404, json={"error": "unexpected " + request.url.path})

    return handler


def _voice_webhook(call_sid=CALL_SID, frm="+15559990000", to="+15550001111", status="ringing"):
    return urlencode(
        {"CallSid": call_sid, "From": frm, "To": to, "CallStatus": status}
    ).encode()


# --- config / capabilities ----------------------------------------------------

def test_requires_conversationrelay_url():
    with pytest.raises(ValueError):
        VoiceProvider(account_sid="AC", auth_token="tok", conversationrelay_url="")


def test_capabilities_are_receive_and_initiate_only():
    provider = _provider()
    assert provider.capabilities == frozenset({"receive", "initiate"})
    assert provider.channel == "voice"


# --- provision ----------------------------------------------------------------

def test_provision_returns_shared_number():
    provider = _provider()
    result = provider.provision(ProvisionRequest("c", "cust", "agt"))
    assert result.address == "+15550001111"
    assert result.provider_resource_id == "+15550001111"


def test_provision_commissions_voice_number_when_no_fixed_from():
    bought = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "AvailablePhoneNumbers" in path:
            assert request.url.params["VoiceEnabled"] == "true"
            return httpx.Response(
                200, json={"available_phone_numbers": [{"phone_number": "+15557778888"}]}
            )
        if path.endswith("/IncomingPhoneNumbers.json"):
            bought.update(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(201, json={"phone_number": "+15557778888", "sid": "PN_SID"})
        return httpx.Response(404)

    provider = _provider(handler, from_number="", inbound_webhook_url="https://gw.example.com/voice")
    result = provider.provision(ProvisionRequest("c", "cust", "agt"))
    assert result.address == "+15557778888"
    assert result.provider_pod_id == "PN_SID"
    assert bought["VoiceUrl"] == "https://gw.example.com/voice"


# --- initiate / send: places a call bridged to ConversationRelay --------------

def test_initiate_places_call_with_conversationrelay_twiml():
    record: list = []
    provider = _provider(_calls_handler(record))
    result = provider.initiate("+15550001111", "+15559990000", OutboundMessage(text="hi"))
    assert result.provider_message_id == f"+15559990000:{CALL_SID}"
    assert result.provider_thread_id == "+15559990000"
    sent = record[0]
    assert sent["From"] == "+15550001111"
    assert sent["To"] == "+15559990000"
    assert "<Connect><ConversationRelay" in sent["Twiml"]
    assert RELAY_URL in sent["Twiml"]


def test_send_places_call_to_recipient():
    record: list = []
    provider = _provider(_calls_handler(record))
    result = provider.send("+15550001111", OutboundMessage(to=("+15551234567",)))
    assert record[0]["To"] == "+15551234567"
    assert result.provider_message_id == f"+15551234567:{CALL_SID}"


def test_connectrelay_twiml_escapes_url():
    twiml = connectrelay_twiml('wss://x.example.com/a?b=1&c=2')
    assert "&amp;" in twiml
    assert twiml.startswith("<?xml")
    assert "<Connect>" in twiml and "ConversationRelay" in twiml


# --- inbound webhook parse ----------------------------------------------------

def test_parse_webhook_makes_incoming_call_event():
    provider = _provider()  # no verify_url -> verification skipped
    msgs = provider.parse_webhook(_voice_webhook(), {})
    assert len(msgs) == 1
    m = msgs[0]
    assert m.chat_type == "voice"
    assert m.text == "[incoming call]"
    assert m.sender_address == "+15559990000"
    assert m.provider_inbox_id == "+15550001111"
    assert m.provider_thread_id == "+15559990000"
    assert m.provider_message_id == f"+15559990000:{CALL_SID}"
    assert m.external_event_id == CALL_SID


def test_parse_webhook_ignores_payload_without_call_sid():
    provider = _provider()
    assert provider.parse_webhook(urlencode({"From": "+1"}).encode(), {}) == []


def test_inbound_twiml_bridges_to_relay():
    provider = _provider()
    assert RELAY_URL in provider.inbound_twiml()
    assert "<Connect>" in provider.inbound_twiml()


# --- signature verification ---------------------------------------------------

def _sign(auth_token: str, url: str, params: dict) -> str:
    signed = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), signed.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_verify_rejects_bad_signature_when_verify_url_set():
    verify_url = "https://gw.example.com/voice"
    provider = _provider(verify_url=verify_url)
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(_voice_webhook(), {"X-Twilio-Signature": "bogus"})


def test_verify_accepts_valid_signature():
    verify_url = "https://gw.example.com/voice"
    provider = _provider(verify_url=verify_url)
    payload = _voice_webhook()
    params = dict(httpx.QueryParams(payload.decode()))
    sig = _sign("tok_test", verify_url, params)
    msgs = provider.parse_webhook(payload, {"X-Twilio-Signature": sig})
    assert len(msgs) == 1 and msgs[0].chat_type == "voice"


# --- connect through the API (provision -> active) ----------------------------

def _app():
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    provider = _provider()
    app = create_app(settings, providers={provider.name: provider})
    return app, TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def _run(app):
    from comm_gateway.jobs import run_pending_jobs  # noqa: PLC0415

    return run_pending_jobs(app.state.session_factory, app.state.providers)


def test_connect_voice_provisions_to_active():
    app, client = _app()
    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Caller"}).json()
    conn = client.post(
        "/v1/connections/voice",
        json={"customer_id": cust["id"], "agent_id": agent["id"]},
    ).json()
    _run(app)
    conn = client.get(f"/v1/connections/{conn['id']}").json()
    assert conn["channel"] == "voice"
    assert conn["status"] == "active"
    assert conn["address"] == "+15550001111"


def test_channels_endpoint_lists_voice_capabilities():
    app, client = _app()
    caps = {c["channel"]: c for c in client.get("/v1/channels").json()}
    assert set(caps["voice"]["capabilities"]) == {"receive", "initiate"}


def test_inbound_call_records_conversation_event():
    app, client = _app()
    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Caller"}).json()
    client.post(
        "/v1/connections/voice",
        json={"customer_id": cust["id"], "agent_id": agent["id"]},
    )
    _run(app)

    r = client.post(
        "/internal/providers/twilio-voice/webhooks",
        content=_voice_webhook(),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 204
    _run(app)
    received = client.get("/v1/events", params={"type": "message.received"}).json()
    msg = received[-1]["data"]["message"]
    assert msg["channel"] == "voice"
    assert msg["chat_type"] == "voice"
    assert msg["text"] == "[incoming call]"
    assert msg["sender"]["address"] == "+15559990000"


def test_initiate_via_api_places_call():
    # provision through the API, then cold-call a recipient over the same provider.
    record: list = []
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False
    )
    provider = _provider(_calls_handler(record))
    app = create_app(settings, providers={provider.name: provider})
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})

    cust = client.post("/v1/customers", json={"name": "Acme"}).json()
    agent = client.post("/v1/agents", json={"name": "Caller"}).json()
    conn = client.post(
        "/v1/connections/voice",
        json={"customer_id": cust["id"], "agent_id": agent["id"]},
    ).json()
    _run(app)
    r = client.post(
        f"/v1/connections/{conn['id']}/initiate",
        json={"recipient": "+15559990000", "text": "calling you"},
    )
    assert r.status_code == 202
    _run(app)
    assert record[0]["To"] == "+15559990000"
    sent = client.get("/v1/events", params={"type": "message.sent"}).json()
    assert len(sent) == 1
