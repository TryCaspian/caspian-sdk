"""XProvider: post tweets + reactive DMs on the X API v2, with mocked HTTP.

Covers the provider surface (post/reply/DM/parse/verify/CRC/provision) and one
connect-through-the-full-gateway path.
"""

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from comm_gateway.providers.x import XProvider
from fastapi.testclient import TestClient

CONSUMER_SECRET = "consumer-secret"
AGENT_USER_ID = "1000000000000000001"  # the connected account (for_user_id)
HUMAN_USER_ID = "42"  # a person who DMs the agent
ACCESS_TOKEN = "USER_ACCESS_TOKEN"


def _provider(handler, **kw):
    """An XProvider whose X API client is backed by `handler`."""
    provider = XProvider(consumer_secret=CONSUMER_SECRET, **kw)
    provider._client = httpx.Client(
        base_url="https://api.x.com",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    return provider


def _creds(**over):
    base = {"access_token": ACCESS_TOKEN, "user_id": AGENT_USER_ID, "username": "agentbot"}
    base.update(over)
    return base


def _dm_webhook_body(sender_id: str, text: str, event_id: str = "9001") -> bytes:
    return json.dumps(
        {
            "for_user_id": AGENT_USER_ID,
            "direct_message_events": [
                {
                    "type": "message_create",
                    "id": event_id,
                    "created_timestamp": "1700000000000",
                    "message_create": {
                        "target": {"recipient_id": AGENT_USER_ID},
                        "sender_id": sender_id,
                        "message_data": {"text": text},
                    },
                }
            ],
            "users": {
                sender_id: {"id": sender_id, "name": "A Human", "screen_name": "ahuman"},
            },
        }
    ).encode()


def _sign(secret: str, payload: bytes) -> str:
    return "sha256=" + base64.b64encode(
        hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    ).decode()


# --- send: post a tweet ------------------------------------------------------

def test_send_posts_a_tweet():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/2/tweets"
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert json.loads(request.content) == {"text": "hello world"}
        return httpx.Response(201, json={"data": {"id": "tweet123", "text": "hello world"}})

    provider = _provider(handler)
    result = provider.send(
        AGENT_USER_ID, OutboundMessage(text="hello world", to=()), credentials=_creds()
    )
    assert len(calls) == 1
    assert result.provider_message_id == "tweet123"
    assert result.provider_thread_id == "tweet123"


def test_oauth1_signing_when_app_keys_configured():
    # With the app consumer pair + the account's token secret, requests are
    # OAuth 1.0a HMAC-SHA1 signed (non-expiring), not bearer.
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(201, json={"data": {"id": "t1", "text": "hi"}})

    provider = _provider(handler, consumer_key="CKEY",
                         access_token="ATOKEN", access_secret="ASECRET",
                         user_id=AGENT_USER_ID)
    provider.send(AGENT_USER_ID, OutboundMessage(text="hi", to=()), credentials=None)
    auth = seen["auth"]
    assert auth.startswith("OAuth ")
    assert 'oauth_signature_method="HMAC-SHA1"' in auth
    assert 'oauth_consumer_key="CKEY"' in auth
    assert 'oauth_token="ATOKEN"' in auth
    assert "oauth_signature=" in auth


def test_falls_back_to_bearer_without_token_secret():
    # Consumer key present but no access_secret -> can't sign OAuth1 -> bearer.
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(201, json={"data": {"id": "t1", "text": "hi"}})

    provider = _provider(handler, consumer_key="CKEY")
    provider.send(AGENT_USER_ID, OutboundMessage(text="hi", to=()), credentials=_creds())
    assert seen["auth"] == f"Bearer {ACCESS_TOKEN}"


def test_send_with_dm_prefix_hits_dm_endpoint():
    def handler(request):
        assert request.url.path == f"/2/dm_conversations/with/{HUMAN_USER_ID}/messages"
        assert json.loads(request.content) == {"text": "hi back"}
        return httpx.Response(
            201, json={"data": {"dm_conversation_id": "conv1", "dm_event_id": "dm777"}}
        )

    provider = _provider(handler)
    result = provider.send(
        AGENT_USER_ID,
        OutboundMessage(text="hi back", to=(f"dm:{HUMAN_USER_ID}",)),
        credentials=_creds(),
    )
    assert result.provider_message_id == f"dm:{HUMAN_USER_ID}:dm777"
    assert result.provider_thread_id == f"dm:{HUMAN_USER_ID}"


# --- reply: reactive DM back -------------------------------------------------

def test_reply_to_dm_hits_dm_endpoint():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.path == f"/2/dm_conversations/with/{HUMAN_USER_ID}/messages"
        assert json.loads(request.content) == {"text": "thanks for reaching out"}
        return httpx.Response(201, json={"data": {"dm_event_id": "dm888"}})

    provider = _provider(handler)
    # inbound DM's provider_message_id is dm:<sender_id>:<event_id>
    result = provider.reply(
        AGENT_USER_ID,
        f"dm:{HUMAN_USER_ID}:9001",
        OutboundMessage(text="thanks for reaching out"),
        credentials=_creds(),
    )
    assert calls == [f"/2/dm_conversations/with/{HUMAN_USER_ID}/messages"]
    assert result.provider_message_id == f"dm:{HUMAN_USER_ID}:dm888"


def test_reply_to_tweet_threads_the_reply():
    def handler(request):
        assert request.url.path == "/2/tweets"
        assert json.loads(request.content) == {
            "text": "replying",
            "reply": {"in_reply_to_tweet_id": "tweet123"},
        }
        return httpx.Response(201, json={"data": {"id": "tweet456"}})

    provider = _provider(handler)
    result = provider.reply(
        AGENT_USER_ID, "tweet123", OutboundMessage(text="replying"), credentials=_creds()
    )
    assert result.provider_message_id == "tweet456"


# --- poll_dms (no-webhook inbound) -------------------------------------------

def _dm_events_page(*events):
    """A GET /2/dm_events response body from (id, sender_id, text) tuples."""
    return {
        "data": [
            {"id": eid, "sender_id": sid, "text": txt, "event_type": "MessageCreate",
             "created_at": "2026-07-16T00:00:00.000Z"}
            for eid, sid, txt in events
        ],
        "includes": {"users": [{"id": HUMAN_USER_ID, "name": "A Human",
                                "username": "ahuman"}]},
    }


def test_poll_dms_first_poll_sets_baseline_emits_nothing():
    # A bot must not answer the DM history it inherits; first poll only baselines.
    page = _dm_events_page(("300", HUMAN_USER_ID, "old one"), ("200", HUMAN_USER_ID, "older"))
    provider = _provider(lambda r: httpx.Response(200, json=page))
    msgs, cursor = provider.poll_dms(_creds(), cursor=None)
    assert msgs == []
    assert cursor == "300"  # newest id becomes the baseline


def test_poll_dms_returns_only_new_in_order():
    page = _dm_events_page(
        ("303", HUMAN_USER_ID, "third"),
        ("302", HUMAN_USER_ID, "second"),
        ("301", HUMAN_USER_ID, "already seen"),
    )
    provider = _provider(lambda r: httpx.Response(200, json=page))
    msgs, cursor = provider.poll_dms(_creds(), cursor="301")
    assert [m.text for m in msgs] == ["second", "third"]  # oldest-first
    assert cursor == "303"
    assert msgs[0].provider_message_id == f"dm:{HUMAN_USER_ID}:302"


def test_poll_dms_skips_own_echoes():
    page = _dm_events_page(
        ("305", AGENT_USER_ID, "the bot's own reply"),
        ("304", HUMAN_USER_ID, "a real question"),
    )
    provider = _provider(lambda r: httpx.Response(200, json=page))
    msgs, cursor = provider.poll_dms(_creds(), cursor="303")
    assert [m.text for m in msgs] == ["a real question"]
    assert cursor == "305"  # cursor still advances past our own echo


# --- one-click OAuth 1.0a 3-legged -------------------------------------------

def test_oauth_request_token_returns_authorize_url():
    def handler(request):
        assert request.url.path == "/oauth/request_token"
        assert "oauth_callback" in request.headers["authorization"]
        return httpx.Response(
            200, text="oauth_token=TOKEN1&oauth_token_secret=SECRET1&oauth_callback_confirmed=true"
        )

    provider = _provider(handler, consumer_key="CK")
    res = provider.oauth_request_token("https://gw.test/cb")
    assert res["oauth_token"] == "TOKEN1"
    assert res["oauth_token_secret"] == "SECRET1"
    assert res["authorize_url"] == "https://api.x.com/oauth/authorize?oauth_token=TOKEN1"


def test_oauth_request_token_raises_when_not_confirmed():
    provider = _provider(
        lambda r: httpx.Response(200, text="oauth_callback_confirmed=false"), consumer_key="CK"
    )
    with pytest.raises(ValueError):
        provider.oauth_request_token("https://gw.test/cb")


def test_oauth_access_token_returns_account_tokens():
    def handler(request):
        assert request.url.path == "/oauth/access_token"
        auth = request.headers["authorization"]
        assert "oauth_verifier" in auth and "oauth_token" in auth
        return httpx.Response(
            200, text="oauth_token=AT1&oauth_token_secret=AS1&user_id=999&screen_name=acmebot"
        )

    provider = _provider(handler, consumer_key="CK")
    res = provider.oauth_access_token("REQTOK", "VERIF", "REQSECRET")
    assert res == {"access_token": "AT1", "access_secret": "AS1",
                   "user_id": "999", "username": "acmebot"}


# --- parse_webhook -----------------------------------------------------------

def test_parse_webhook_turns_dm_into_inbound():
    provider = _provider(lambda r: httpx.Response(404))
    body = _dm_webhook_body(HUMAN_USER_ID, "hey agent")
    headers = {"x-twitter-webhooks-signature": _sign(CONSUMER_SECRET, body)}
    inbound = provider.parse_webhook(body, headers)
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "hey agent"
    assert msg.sender_address == HUMAN_USER_ID
    assert msg.sender_name == "A Human"
    assert msg.provider_inbox_id == AGENT_USER_ID
    assert msg.provider_thread_id == f"dm:{HUMAN_USER_ID}"
    assert msg.provider_message_id == f"dm:{HUMAN_USER_ID}:9001"
    assert msg.chat_type == "x_dm"


def test_parse_webhook_skips_own_echo():
    provider = _provider(lambda r: httpx.Response(404))
    # A DM whose sender IS the agent (an echo of its own outbound) must be dropped.
    body = _dm_webhook_body(AGENT_USER_ID, "message the agent itself sent")
    headers = {"x-twitter-webhooks-signature": _sign(CONSUMER_SECRET, body)}
    assert provider.parse_webhook(body, headers) == []


# --- webhook signature verification -----------------------------------------

def test_parse_webhook_good_signature_passes():
    provider = _provider(lambda r: httpx.Response(404))
    body = _dm_webhook_body(HUMAN_USER_ID, "verified")
    headers = {"x-twitter-webhooks-signature": _sign(CONSUMER_SECRET, body)}
    assert len(provider.parse_webhook(body, headers)) == 1


def test_parse_webhook_bad_signature_raises():
    provider = _provider(lambda r: httpx.Response(404))
    body = _dm_webhook_body(HUMAN_USER_ID, "tampered")
    headers = {"x-twitter-webhooks-signature": _sign("wrong-secret", body)}
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(body, headers)


def test_parse_webhook_no_secret_skips_verification():
    # Opt-in: with no consumer secret configured, signatures are not required.
    provider = XProvider(consumer_secret="")
    body = _dm_webhook_body(HUMAN_USER_ID, "unsigned")
    assert len(provider.parse_webhook(body, {})) == 1


# --- CRC challenge -----------------------------------------------------------

def test_verify_challenge_signs_crc_token():
    provider = XProvider(consumer_secret=CONSUMER_SECRET)
    token = provider.verify_challenge({"crc_token": "abc123"})
    expected = "sha256=" + base64.b64encode(
        hmac.new(CONSUMER_SECRET.encode(), b"abc123", hashlib.sha256).digest()
    ).decode()
    assert token == {"response_token": expected}


def test_verify_challenge_none_without_token():
    provider = XProvider(consumer_secret=CONSUMER_SECRET)
    assert provider.verify_challenge({}) is None


# --- provision ---------------------------------------------------------------

def test_provision_returns_user_id_as_resource_id():
    provider = _provider(lambda r: httpx.Response(404))
    result = provider.provision(
        ProvisionRequest("c", "cust", "agt", credentials=_creds())
    )
    assert result.provider_resource_id == AGENT_USER_ID
    assert result.address == "agentbot"


def test_provision_requires_user_id():
    provider = XProvider()
    with pytest.raises(ValueError):
        provider.provision(ProvisionRequest("c", "cust", "agt", credentials={}))


# --- connect through the full gateway stack ----------------------------------

API_KEY = "comm_x_key"


def _seed_account(app, api_key):
    """X runs on Caspian's paid network, so connecting it needs credit. These
    tests exercise connect logic, not billing, so fund the bootstrap project's
    account explicitly (new signups get no free grant)."""
    from comm_gateway.auth import hash_key
    from comm_gateway.crypto import _encrypt
    from comm_gateway.models import ApiKey, DashboardAccount
    from sqlalchemy import select

    with app.state.session_factory() as s:
        pid = s.execute(
            select(ApiKey.project_id).where(ApiKey.key_hash == hash_key(api_key))
        ).scalar_one()
        s.add(DashboardAccount(
            email="x-dev@example.com", project_id=pid,
            api_key_enc=_encrypt({"api_key": api_key}), credit_cents=10000,
        ))
        s.commit()


@pytest.fixture()
def x_app():
    settings = Settings(
        database_url="sqlite://",
        bootstrap_api_key=API_KEY,
        inline_worker=False,
    )
    provider = _provider(lambda r: httpx.Response(404))
    app = create_app(settings, providers={provider.name: provider})
    _seed_account(app, API_KEY)
    return app


@pytest.fixture()
def xc(x_app):
    return TestClient(x_app, headers={"Authorization": f"Bearer {API_KEY}"})


def _run(app):
    from comm_gateway.jobs import run_pending_jobs

    run_pending_jobs(app.state.session_factory, app.state.providers)


def test_connect_x_through_api(x_app, xc):
    resp = xc.post(
        "/v1/connections/x",
        json={"access_token": ACCESS_TOKEN, "user_id": AGENT_USER_ID, "username": "agentbot"},
    )
    assert resp.status_code == 201
    conn = resp.json()
    assert conn["status"] == "provisioning"
    assert conn["channel"] == "x"
    # provision reads the account id from credentials (no HTTP), goes active.
    _run(x_app)
    conn = xc.get(f"/v1/connections/{conn['id']}").json()
    assert conn["status"] == "active"
    assert conn["address"] == "agentbot"
    # capabilities: receive/reply/send, never initiate (no cold outreach).
    assert "initiate" not in conn["capabilities"]
    assert set(conn["capabilities"]) >= {"receive", "reply", "send"}


def test_connect_x_requires_access_token_and_user_id(x_app, xc):
    r = xc.post("/v1/connections/x", json={"user_id": AGENT_USER_ID})
    assert r.status_code == 422
    assert "access_token" in r.json()["detail"]


def test_connect_x_is_idempotent(x_app, xc):
    first = xc.post(
        "/v1/connections/x",
        json={"access_token": ACCESS_TOKEN, "user_id": AGENT_USER_ID},
    ).json()
    again = xc.post(
        "/v1/connections/x",
        json={"access_token": ACCESS_TOKEN, "user_id": AGENT_USER_ID},
    ).json()
    assert first["id"] == again["id"]


def test_same_x_account_conflicts_across_scopes(x_app, xc):
    # One X account maps to exactly one connection (inbound routes by user id):
    # connecting the same account under a different scope is a 409.
    xc.post(
        "/v1/connections/x",
        json={"access_token": ACCESS_TOKEN, "user_id": AGENT_USER_ID},
    )
    customer = xc.post("/v1/customers", json={"name": "cust2"}).json()
    agent = xc.post("/v1/agents", json={"name": "agent2"}).json()
    r = xc.post(
        "/v1/connections/x",
        json={
            "access_token": "another-token",
            "user_id": AGENT_USER_ID,
            "customer_id": customer["id"],
            "agent_id": agent["id"],
        },
    )
    assert r.status_code == 409


def _oauth_app():
    """An app whose X provider mocks the two OAuth 1.0a legs + is shared-app-ready."""
    def handler(request):
        p = request.url.path
        if p == "/oauth/request_token":
            return httpx.Response(
                200,
                text="oauth_token=REQTOK&oauth_token_secret=REQSEC&oauth_callback_confirmed=true",
            )
        if p == "/oauth/access_token":
            return httpx.Response(
                200,
                text="oauth_token=1562-abc&oauth_token_secret=SEC&user_id=1562&screen_name=acmebot",
            )
        return httpx.Response(404)

    provider = _provider(handler, consumer_key="CK")
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    app = create_app(settings, providers={provider.name: provider})
    _seed_account(app, API_KEY)  # X is paid; fund the project so install passes the gate
    return app


def test_install_x_one_click_flow():
    app = _oauth_app()
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})

    # 1. install -> a "Sign in with X" authorize_url, no tokens pasted
    r = sc.post("/v1/connections/x/install", json={})
    assert r.status_code == 201
    out = r.json()
    assert out["status"] == "pending_oauth"
    assert out["authorize_url"] == "https://api.x.com/oauth/authorize?oauth_token=REQTOK"

    # 2. X redirects to the callback with oauth_token + verifier
    anon = TestClient(app)
    cb = anon.get("/v1/oauth/x/callback",
                  params={"oauth_token": "REQTOK", "oauth_verifier": "VERIF"})
    assert cb.status_code == 200 and "Connected" in cb.text

    # 3. provision -> active; the account's non-expiring tokens are now stored
    from comm_gateway.jobs import run_pending_jobs
    run_pending_jobs(app.state.session_factory, app.state.providers)
    conn = sc.get(f"/v1/connections/{out['id']}").json()
    assert conn["status"] == "active"
    assert conn["address"] == "acmebot"
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        creds = session.get(Connection, out["id"]).provider_credentials
        assert creds["access_token"] == "1562-abc"
        assert creds["access_secret"] == "SEC"
        assert creds["user_id"] == "1562"
        assert creds["username"] == "acmebot"


def test_install_x_requires_shared_app_configured():
    provider = _provider(lambda r: httpx.Response(404))  # no consumer_key = not configured
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False)
    app = create_app(settings, providers={provider.name: provider})
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = sc.post("/v1/connections/x/install", json={})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]
