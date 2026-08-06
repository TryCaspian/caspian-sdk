"""Signal via a self-hosted signal-cli daemon — unit + registry wiring.

Mocks the signal-cli JSON-RPC daemon with httpx.MockTransport (no live daemon,
no network).
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
from comm_gateway.providers.registry import _build_one
from comm_gateway.providers.signal import (
    RPC_PATH,
    SignalProvider,
    parse_envelope,
    target_params,
)

BASE_URL = "http://localhost:8080"
NUMBER = "+15550001111"
PEER = "+15557776666"
GROUP_ID = "Z3JvdXAtaWQ="  # base64, as signal-cli reports it
SEND_TIMESTAMP = 1700000000000


def _handler(calls: list) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == RPC_PATH
        body = json.loads(request.content)
        assert body["jsonrpc"] == "2.0"
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"timestamp": SEND_TIMESTAMP, "results": []},
            },
        )

    return httpx.MockTransport(handle)


def _provider(calls: list, webhook_secret: str = "") -> SignalProvider:
    p = SignalProvider(base_url=BASE_URL, number=NUMBER, webhook_secret=webhook_secret)
    p._client = httpx.Client(base_url=BASE_URL, transport=_handler(calls), timeout=5.0)
    return p


# --- helpers -----------------------------------------------------------------

def test_target_params_splits_dm_from_group():
    assert target_params(PEER) == {"recipient": [PEER]}
    assert target_params(GROUP_ID) == {"groupId": GROUP_ID}


# --- construction ------------------------------------------------------------

def test_missing_required_config_raises():
    with pytest.raises(ValueError):
        SignalProvider(base_url="", number=NUMBER)
    with pytest.raises(ValueError):
        SignalProvider(base_url=BASE_URL, number="")


def test_provision_returns_configured_number():
    p = _provider([])
    res = p.provision(ProvisionRequest("c", "cu", "ag"))
    assert res.address == NUMBER
    assert res.provider_resource_id == NUMBER


# --- outbound ----------------------------------------------------------------

def test_send_addresses_a_recipient_and_returns_composite_id():
    calls: list = []
    p = _provider(calls)
    res = p.send("inbox", OutboundMessage(text="hi there", to=(PEER,)))
    assert calls[0]["method"] == "send"
    assert calls[0]["params"] == {"recipient": [PEER], "message": "hi there"}
    assert res.provider_message_id == f"{PEER}:{SEND_TIMESTAMP}"
    assert res.provider_thread_id == PEER


def test_reply_routes_back_to_the_same_thread():
    calls: list = []
    p = _provider(calls)
    res = p.reply("inbox", f"{PEER}:1699999999999", OutboundMessage(text="re"))
    assert calls[0]["params"] == {"recipient": [PEER], "message": "re"}
    assert res.provider_thread_id == PEER


def test_reply_into_a_group_uses_group_id():
    calls: list = []
    p = _provider(calls)
    p.reply("inbox", f"{GROUP_ID}:1699999999999", OutboundMessage(text="re all"))
    assert calls[0]["params"] == {"groupId": GROUP_ID, "message": "re all"}


def test_initiate_cold_starts_to_a_number():
    calls: list = []
    p = _provider(calls)
    res = p.initiate("inbox", "+15551230000", OutboundMessage(text="first contact"))
    assert calls[0]["params"] == {"recipient": ["+15551230000"], "message": "first contact"}
    assert res.provider_message_id == f"+15551230000:{SEND_TIMESTAMP}"


def test_jsonrpc_error_is_raised_not_swallowed():
    # signal-cli reports failures in-band with HTTP 200.
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -1, "message": "nope"}}
        )

    p = SignalProvider(base_url=BASE_URL, number=NUMBER)
    p._client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handle), timeout=5.0)
    with pytest.raises(RuntimeError):
        p.send("inbox", OutboundMessage(text="hi", to=(PEER,)))


# --- inbound normalization ---------------------------------------------------

def _envelope(message: str | None = "hello agent", group: bool = False) -> dict:
    data: dict = {"timestamp": 1700000000001, "message": message}
    if group:
        data["groupInfo"] = {"groupId": GROUP_ID, "type": "DELIVER"}
    return {
        "envelope": {
            "source": PEER,
            "sourceNumber": PEER,
            "sourceName": "Alice",
            "timestamp": 1700000000001,
            "dataMessage": data,
        },
        "account": NUMBER,
    }


def test_parse_envelope_normalizes_a_dm():
    msgs = parse_envelope(_envelope(), NUMBER)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.text == "hello agent"
    assert m.sender_address == PEER
    assert m.sender_name == "Alice"
    assert m.provider_inbox_id == NUMBER
    assert m.provider_thread_id == PEER
    assert m.provider_message_id == f"{PEER}:1700000000001"
    assert m.external_event_id == "1700000000001"
    assert m.chat_type == "private"


def test_parse_envelope_threads_a_group_on_group_id():
    m = parse_envelope(_envelope(group=True), NUMBER)[0]
    assert m.provider_thread_id == GROUP_ID
    assert m.provider_message_id == f"{GROUP_ID}:1700000000001"
    assert m.chat_type == "group"
    assert m.sender_address == PEER  # still attributed to the human who spoke


def test_parse_envelope_unwraps_the_jsonrpc_notification():
    # The receive subscription streams the envelope inside `params`.
    notification = {"jsonrpc": "2.0", "method": "receive", "params": _envelope()}
    assert parse_envelope(notification, NUMBER)[0].text == "hello agent"


def test_parse_envelope_ignores_receipts_and_typing():
    receipt = {"envelope": {"source": PEER, "timestamp": 1, "receiptMessage": {"isDelivery": True}}}
    typing = {"envelope": {"source": PEER, "timestamp": 1, "typingMessage": {"action": "STARTED"}}}
    assert parse_envelope(receipt, NUMBER) == []
    assert parse_envelope(typing, NUMBER) == []


def test_parse_envelope_ignores_textless_data_messages():
    # Attachment- or reaction-only: this provider claims neither MEDIA nor
    # REACTIONS, so there is nothing to hand the agent.
    assert parse_envelope(_envelope(message=None), NUMBER) == []


# --- opt-in bridge secret ----------------------------------------------------

def _payload() -> bytes:
    return json.dumps(_envelope()).encode()


def test_secret_enforced_only_when_configured():
    p = _provider([])
    assert len(p.parse_webhook(_payload(), {})) == 1


def test_bad_or_missing_secret_rejected():
    p = _provider([], webhook_secret="hunter2")
    with pytest.raises(WebhookVerificationError):
        p.parse_webhook(_payload(), {})  # missing header
    with pytest.raises(WebhookVerificationError):
        p.parse_webhook(_payload(), {"x-signal-secret": "wrong"})


def test_good_secret_parses():
    p = _provider([], webhook_secret="hunter2")
    msgs = p.parse_webhook(_payload(), {"X-Signal-Secret": "hunter2"})
    assert len(msgs) == 1


# --- registry wiring ---------------------------------------------------------

def test_registry_builds_provider_from_settings():
    settings = Settings(
        database_url="sqlite://",
        signal_cli_url=BASE_URL,
        signal_number=NUMBER,
    )
    provider = _build_one("signal-cli", settings)
    assert provider.name == "signal-cli"
    assert provider.channel == "signal"
    assert {"receive", "reply", "send", "initiate"} == set(provider.capabilities)


def test_registry_missing_config_raises():
    settings = Settings(database_url="sqlite://")  # nothing set
    with pytest.raises(ValueError):
        _build_one("signal-cli", settings)


def test_registry_builds_the_offline_fake():
    provider = _build_one("fake-signal", Settings(database_url="sqlite://"))
    assert provider.channel == "signal"
    assert provider.capabilities == SignalProvider.capabilities
    # The fake consumes the real signal-cli payload shape.
    assert provider.parse_webhook(_payload(), {})[0].text == "hello agent"


# --- through the connect API (no per-connection credentials) -----------------

def test_connect_signal_slots_in_via_the_generic_endpoint():
    from comm_gateway.jobs import run_pending_jobs
    from comm_gateway.main import create_app
    from fastapi.testclient import TestClient

    api_key = "comm_test_key"
    settings = Settings(
        database_url="sqlite://",
        providers="fake-signal",
        bootstrap_api_key=api_key,
        inline_worker=False,
    )
    app = create_app(settings)
    client = TestClient(app, headers={"Authorization": f"Bearer {api_key}"})

    # Signal needs no per-connection credentials (like email): the generic
    # /v1/connections/signal endpoint serves it.
    conn = client.post("/v1/connections/signal", json={}).json()
    assert run_pending_jobs(app.state.session_factory, app.state.providers) >= 1
    active = client.get(f"/v1/connections/{conn['id']}").json()
    assert active["status"] == "active"

    caps = {c["provider"]: c for c in client.get("/v1/channels").json()}
    assert set(caps["fake-signal"]["capabilities"]) == {"receive", "reply", "send", "initiate"}
