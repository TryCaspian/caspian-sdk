"""Multi-tenant MetaWhatsAppProvider + Graph onboarding helpers, mocked Graph."""

import hashlib
import hmac
import json

import httpx
import pytest
from comm_gateway.providers.base import ProvisionRequest, WebhookVerificationError
from comm_gateway.providers.meta_onboarding import MetaOnboardingError
from comm_gateway.providers.meta_whatsapp import MetaWhatsAppProvider

APP_SECRET = "app-secret"


def _graph(handler):
    """A MetaWhatsAppProvider whose Graph client is backed by `handler`."""
    provider = MetaWhatsAppProvider(app_secret=APP_SECRET, verify_token="vt")
    provider._client = httpx.Client(
        base_url="https://graph.facebook.com/v21.0",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    return provider


def _default_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/oauth/access_token"):
        assert request.url.params["code"] == "the-code"
        return httpx.Response(200, json={"access_token": "BUSINESS_TOKEN"})
    if path.endswith("/subscribed_apps"):
        assert request.headers["authorization"] == "Bearer PER_CONN_TOKEN"
        return httpx.Response(200, json={"success": True})
    if path.endswith("/register"):
        return httpx.Response(200, json={"success": True})
    if path.endswith("/messages"):
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})
    return httpx.Response(404, json={"error": {"message": "unexpected " + path}})


def _creds(**over):
    base = {"phone_number_id": "PN1", "waba_id": "WABA1", "access_token": "PER_CONN_TOKEN"}
    base.update(over)
    return base


# --- onboarding helpers ------------------------------------------------------

def test_exchange_code_success():
    provider = _graph(_default_handler)
    assert provider.exchange_signup_code("app", "sec", "the-code") == "BUSINESS_TOKEN"


def test_exchange_code_failure():
    provider = _graph(lambda r: httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(MetaOnboardingError):
        provider.exchange_signup_code("app", "sec", "nope")


def test_register_number_idempotent_already_registered():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/register"):
            seen["registered"] = True
            return httpx.Response(400, json={"error": {"code": 133005, "message": "already"}})
        return _default_handler(request)

    provider = _graph(handler)
    # provision must NOT raise even though register returns "already registered"
    result = provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    assert result.provider_resource_id == "PN1"
    assert seen["registered"]


# --- multi-tenant send/reply -------------------------------------------------

def test_send_uses_per_connection_number_and_token():
    calls = []

    def handler(request):
        if request.url.path.endswith("/messages"):
            calls.append((request.url.path, request.headers["authorization"],
                          json.loads(request.content)))
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        return _default_handler(request)

    provider = _graph(handler)
    from comm_gateway.providers.base import OutboundMessage

    provider.send("PN1", OutboundMessage(text="hi", to=("+15551230000",)), credentials=_creds())
    path, auth, body = calls[0]
    assert path.endswith("/PN1/messages")           # per-connection number
    assert auth == "Bearer PER_CONN_TOKEN"           # per-connection token
    assert body["to"] == "+15551230000"


def test_send_falls_back_to_config_number():
    provider = MetaWhatsAppProvider(
        app_secret=APP_SECRET, phone_number_id="DEFAULT_PN", access_token="DEFAULT_TOK"
    )
    calls = []

    def handler(request):
        calls.append((request.url.path, request.headers["authorization"]))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    provider._client = httpx.Client(
        base_url="https://graph.facebook.com/v21.0",
        transport=httpx.MockTransport(handler), timeout=5.0,
    )
    from comm_gateway.providers.base import OutboundMessage

    provider.send("x", OutboundMessage(text="hi", to=("+1",)), credentials=None)
    assert calls[0][0].endswith("/DEFAULT_PN/messages")
    assert calls[0][1] == "Bearer DEFAULT_TOK"


def test_send_without_any_credentials_raises():
    provider = MetaWhatsAppProvider(app_secret=APP_SECRET)  # no fallback configured
    from comm_gateway.providers.base import OutboundMessage

    with pytest.raises(ValueError):
        provider.send("x", OutboundMessage(text="hi", to=("+1",)), credentials={})


# --- provision ----------------------------------------------------------------

def test_provision_subscribes_and_registers():
    hit = set()

    def handler(request):
        if request.url.path.endswith("/subscribed_apps"):
            hit.add("subscribe")
        if request.url.path.endswith("/register"):
            hit.add("register")
        return _default_handler(request)

    provider = _graph(handler)
    result = provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    assert result.provider_resource_id == "PN1"
    assert result.address == "PN1"
    assert hit == {"subscribe", "register"}


def test_provision_subscribe_failure_raises():
    def handler(request):
        if request.url.path.endswith("/subscribed_apps"):
            return httpx.Response(400, json={"error": {"message": "no"}})
        return _default_handler(request)

    provider = _graph(handler)
    with pytest.raises(MetaOnboardingError):
        provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))


# --- webhook parse + verify ---------------------------------------------------

def _webhook_body(pnid="PN1", frm="+15559990000", text="hello"):
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": pnid},
                                "messages": [
                                    {"id": "wamid.in", "from": frm, "type": "text",
                                     "text": {"body": text}}
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_parse_webhook_valid_signature():
    provider = _graph(_default_handler)
    body = _webhook_body()
    msgs = provider.parse_webhook(
        body, {"X-Hub-Signature-256": _sig(body)}, credentials=_creds()
    )
    assert len(msgs) == 1 and msgs[0].text == "hello"
    assert msgs[0].provider_inbox_id == "PN1"


def test_parse_webhook_bad_signature_rejected():
    provider = _graph(_default_handler)
    body = _webhook_body()
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(body, {"X-Hub-Signature-256": "sha256=deadbeef"},
                               credentials=_creds())


def test_parse_webhook_skips_message_missing_from_or_id():
    """A malformed message (missing "from" or "id") must be skipped, not raise
    a KeyError and 500 the whole delivery - the other, well-formed message in
    the same batch still comes through."""
    provider = _graph(_default_handler)
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "PN1"},
                                "messages": [
                                    {"id": "wamid.missing_from", "type": "text",
                                     "text": {"body": "no sender"}},
                                    {"from": "+15551234567", "type": "text",
                                     "text": {"body": "no id"}},
                                    {"id": "wamid.ok", "from": "+15559990000",
                                     "type": "text", "text": {"body": "hello"}},
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    msgs = provider.parse_webhook(
        body, {"X-Hub-Signature-256": _sig(body)}, credentials=_creds()
    )
    assert len(msgs) == 1
    assert msgs[0].text == "hello"


def test_parse_webhook_multitenant_no_fallback_no_credentials():
    # The unscoped route (one Meta app, many WABAs) hands parse_webhook
    # credentials=None. A pure multi-tenant deployment has no COMM_META_WA_*
    # fallback, so _creds() can't resolve - parse must not raise and must route
    # by the phone_number_id carried in the payload metadata.
    provider = _graph(_default_handler)  # no default_phone_number_id configured
    body = _webhook_body(pnid="PN_TENANT")
    msgs = provider.parse_webhook(body, {"X-Hub-Signature-256": _sig(body)}, credentials=None)
    assert len(msgs) == 1
    assert msgs[0].provider_inbox_id == "PN_TENANT"


def test_meta_verify_challenge():
    provider = _graph(_default_handler)
    ok = provider.meta_verify(
        {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "12345"}
    )
    assert ok == "12345"
    assert provider.meta_verify({"hub.mode": "subscribe", "hub.verify_token": "wrong"}) is None
