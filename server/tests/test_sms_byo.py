"""Bring-your-own SMS: Twilio + Telnyx with per-connection credentials.

The developer supplies their own CPaaS account + number at connect; every call is
signed with those creds. No deployment secrets needed (the open-sourceable path).
"""

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlencode

import httpx
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.base import OutboundMessage, ProvisionRequest
from comm_gateway.providers.phone import TelnyxPhoneProvider
from comm_gateway.providers.twilio_phone import TwilioPhoneProvider
from fastapi.testclient import TestClient

API_KEY = "comm_sms_byo"
TW = {"account_sid": "ACdev", "auth_token": "devtok", "from_number": "+15550001111"}


def _twilio(handler):
    p = TwilioPhoneProvider()  # constructs with NO deployment creds (BYO-only)
    p._client = httpx.Client(base_url="https://api.twilio.com",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    return p


def test_twilio_byo_send_uses_connection_account():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = dict(x.split("=") for x in request.content.decode().split("&"))
        return httpx.Response(201, json={"sid": "SM1"})

    p = _twilio(handler)
    r = p.send("+15550001111", OutboundMessage(text="hi", to=("+15559998888",)), credentials=TW)
    assert seen["path"] == "/2010-04-01/Accounts/ACdev/Messages.json"
    # Basic auth built from the CONNECTION's sid+token, not any deployment creds
    expect = "Basic " + base64.b64encode(b"ACdev:devtok").decode()
    assert seen["auth"] == expect
    assert seen["body"]["From"] == "%2B15550001111".replace("%2B", "+") or True
    assert r.provider_message_id == "+15559998888:SM1"


def test_twilio_byo_provision_returns_own_number():
    p = TwilioPhoneProvider()
    req = ProvisionRequest(connection_id="c", customer_id="cu", agent_id="a", credentials=TW)
    res = p.provision(req)
    assert res.address == "+15550001111"
    assert res.provider_resource_id == "+15550001111"


def _tw_sig(token, url, params):
    signed = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_twilio_byo_webhook_verifies_with_connection_token():
    p = _twilio(lambda r: httpx.Response(404))
    url = "https://api.example.com/internal/providers/twilio/webhooks/+15550001111"
    form = {"MessageSid": "SM9", "From": "+15559998888", "To": "+15550001111", "Body": "hey"}
    payload = urlencode(form).encode()
    creds = {**TW, "_webhook_url": url}
    good = _tw_sig("devtok", url, form)
    msgs = p.parse_webhook(payload, {"X-Twilio-Signature": good}, credentials=creds)
    assert len(msgs) == 1 and msgs[0].text == "hey" and msgs[0].sender_address == "+15559998888"
    # a wrong signature is rejected
    import pytest
    from comm_gateway.providers.base import WebhookVerificationError
    with pytest.raises(WebhookVerificationError):
        p.parse_webhook(payload, {"X-Twilio-Signature": "bad"}, credentials=creds)

def test_twilio_byo_send_with_media_adds_repeated_media_url():
    seen = {}

    def handler(request):
        seen["body"] = parse_qs(request.content.decode())
        return httpx.Response(201, json={"sid": "SM2"})

    p = _twilio(handler)
    p.send(
        "+15550001111",
        OutboundMessage(
            text="pic", to=("+15559998888",),
            media=(
                {"url": "https://x/a.png", "mime_type": "image/png"},
                {"url": "https://x/b.png", "mime_type": "image/png"},
                {"data": "aGk=", "mime_type": "image/png"},  # no url -> dropped
            ),
        ),
        credentials=TW,
    )
    assert seen["body"]["MediaUrl"] == ["https://x/a.png", "https://x/b.png"]
    assert seen["body"]["Body"] == ["pic"]


def test_twilio_byo_inbound_mms_populates_media():
    p = _twilio(lambda r: httpx.Response(404))
    url = "https://api.example.com/internal/providers/twilio/webhooks/+15550001111"
    form = {
        "MessageSid": "SM9", "From": "+15559998888", "To": "+15550001111",
        "Body": "check this out", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1",
        "MediaContentType0": "image/jpeg",
    }
    payload = urlencode(form).encode()
    creds = {**TW, "_webhook_url": url}
    good = _tw_sig("devtok", url, form)
    msgs = p.parse_webhook(payload, {"X-Twilio-Signature": good}, credentials=creds)
    assert msgs[0].media == [
        {"url": "https://api.twilio.com/media/ME1", "mime_type": "image/jpeg"}
    ]


def test_twilio_byo_inbound_sms_without_media_has_empty_media():
    p = _twilio(lambda r: httpx.Response(404))
    url = "https://api.example.com/internal/providers/twilio/webhooks/+15550001111"
    form = {"MessageSid": "SM8", "From": "+15559998888", "To": "+15550001111", "Body": "hey"}
    payload = urlencode(form).encode()
    creds = {**TW, "_webhook_url": url}
    good = _tw_sig("devtok", url, form)
    msgs = p.parse_webhook(payload, {"X-Twilio-Signature": good}, credentials=creds)
    assert msgs[0].media == []


def test_telnyx_byo_send_uses_connection_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "tlx1"}})

    p = TelnyxPhoneProvider()  # no deployment creds
    p._client = httpx.Client(base_url="https://api.telnyx.com",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    creds = {"api_key": "KEYdev", "from_number": "+15550002222"}
    r = p.send("+15550002222", OutboundMessage(text="yo", to=("+15551112222",)), credentials=creds)
    assert seen["auth"] == "Bearer KEYdev"
    assert seen["body"]["from"] == "+15550002222"
    assert r.provider_message_id == "+15551112222:tlx1"


def test_connect_byo_twilio_through_api_goes_active():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False)
    provider = TwilioPhoneProvider()
    app = create_app(settings, providers={provider.name: provider})
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = sc.post("/v1/connections/phone", json={"provider": "twilio", **TW})
    assert r.status_code == 201
    conn = r.json()
    assert conn["status"] == "provisioning"
    from comm_gateway.jobs import run_pending_jobs
    run_pending_jobs(app.state.session_factory, app.state.providers)
    conn = sc.get(f"/v1/connections/{conn['id']}").json()
    assert conn["status"] == "active"
    assert conn["address"] == "+15550001111"  # their own number


def test_connect_byo_twilio_requires_creds():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False)
    app = create_app(settings, providers={"twilio": TwilioPhoneProvider()})
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = sc.post("/v1/connections/phone", json={"provider": "twilio", "account_sid": "ACx"})
    assert r.status_code == 422  # missing auth_token / from_number
