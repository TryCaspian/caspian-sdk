"""Shared Slack app: one-click install_slack, team-based routing, custom branding.

Mirrors the shared Discord bot: one Caspian Slack app, installed into each
workspace via OAuth; messages route by team_id to the developer's agent, and the
agent posts under the developer's own name + icon.
"""

import httpx
from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_social import FakeSlackProvider
from comm_gateway.providers.slack import SlackProvider, parse_slash_command
from fastapi.testclient import TestClient

API_KEY = "comm_slack_shared"


def _app():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    provider = FakeSlackProvider()  # client_id set = shared app "configured"
    app = create_app(settings, providers={provider.name: provider})
    return app, provider


def _run(app):
    run_pending_jobs(app.state.session_factory, app.state.providers)


def test_slash_command_normalizes_app_and_workspace_routing():
    event = parse_slash_command(
        {
            "api_app_id": "A123",
            "team_id": "T123",
            "channel_id": "C123",
            "user_id": "U123",
            "user_name": "customer",
            "command": "/triage",
            "text": "urgent",
            "trigger_id": "trigger-1",
        }
    )[0]

    assert event.kind == "command"
    assert event.command == "triage"
    assert event.text == "urgent"
    assert event.provider_inbox_id == "A123:T123"


def test_webhook_rejects_stale_timestamp():
    """A correctly-signed Slack request is still rejected once its timestamp is
    older than the allowed skew, so a captured request can't be replayed later."""
    import hashlib
    import hmac
    import json
    import time

    from comm_gateway.providers.base import WebhookVerificationError
    from comm_gateway.providers.slack import MAX_TIMESTAMP_SKEW

    secret = "shhh"
    provider = SlackProvider(client_id="c", signing_secret=secret)
    body = json.dumps({
        "type": "event_callback", "team_id": "T1",
        "event": {"type": "message", "channel": "C1", "ts": "1.1", "user": "U1", "text": "hi"},
    }).encode()

    def signed(ts: str) -> dict:
        base = f"v0:{ts}:".encode() + body
        sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}

    # Fresh timestamp + valid signature -> accepted.
    fresh = provider.parse_webhook(body, signed(str(int(time.time()))))
    assert fresh and fresh[0].text == "hi"

    # Stale timestamp -> rejected even though the signature is valid (replay guard).
    stale = str(int(time.time()) - MAX_TIMESTAMP_SKEW - 60)
    try:
        provider.parse_webhook(body, signed(stale))
        raise AssertionError("expected stale timestamp to be rejected")
    except WebhookVerificationError:
        pass


def test_install_slack_then_callback_branding_and_team_routing():
    app, provider = _app()
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})

    # one-click install with the developer's own name + icon
    r = sc.post("/v1/connections/slack/install",
                json={"display_name": "Acme Support", "icon_url": "https://x/i.png"})
    assert r.status_code == 201
    conn = r.json()
    assert conn["status"] == "pending_oauth"
    assert conn["authorize_url"].startswith("https://slack.com/oauth")

    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        state = session.get(Connection, conn["id"]).provider_credentials["oauth_state"]

    anon = TestClient(app)
    cb = anon.get(f"/v1/oauth/{provider.name}/callback",
                  params={"code": "abc", "state": state}, follow_redirects=True)
    assert cb.status_code == 200 and "Connected" in cb.text
    _run(app)
    active = sc.get(f"/v1/connections/{conn['id']}").json()
    assert active["status"] == "active"

    # the branding survived the OAuth exchange
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        creds = session.get(Connection, conn["id"]).provider_credentials
        assert creds.get("display_name") == "Acme Support"
        assert creds.get("icon_url") == "https://x/i.png"

    # inbound routes by workspace (team_id) to this connection
    payload = provider.webhook_payload(channel="C1", text="hi team")
    hook = anon.post(f"/internal/providers/{provider.name}/webhooks", json=payload)
    assert hook.status_code == 204
    _run(app)
    events = sc.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hi team"
    assert events[-1]["data"]["connection_id"] == conn["id"]


def test_update_branding_after_install():
    app, provider = _app()
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = sc.post("/v1/connections/slack/install",
                   json={"display_name": "Old Name"}).json()
    # change the name/icon later - no re-install
    r = sc.patch(f"/v1/connections/{conn['id']}",
                 json={"display_name": "New Name", "icon_url": "https://x/new.png"})
    assert r.status_code == 200
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        creds = session.get(Connection, conn["id"]).provider_credentials
        assert creds["display_name"] == "New Name"
        assert creds["icon_url"] == "https://x/new.png"


def test_update_branding_wrong_project_404():
    app, provider = _app()
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = sc.patch("/v1/connections/conn_does_not_exist", json={"display_name": "X"})
    assert r.status_code == 404


def test_install_requires_shared_app_configured():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY, inline_worker=False)
    # a Slack provider with NO shared client_id
    app = create_app(settings, providers={"slack": SlackProvider()})
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    r = sc.post("/v1/connections/slack/install", json={})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]


def _pool_apps(n=4):
    return [{"app_id": f"A{i}", "client_id": f"cid{i}",
             "client_secret": f"cs{i}", "signing_secret": f"sig{i}"} for i in range(n)]


def test_pool_size_and_lookup_helpers():
    p = SlackProvider(apps=_pool_apps(4))
    assert p.pool_size() == 4
    assert p.client_id == "cid0"  # shared app "configured" via first pool app
    assert p.app_at(1)["client_id"] == "cid1"
    assert p.app_at(99)["client_id"] == "cid3"  # clamps to last, never IndexError
    assert p._app_by_id("A2")["signing_secret"] == "sig2"
    assert p._app_by_id("nope")["app_id"] == "A0"  # falls back to first


def test_route_key_and_inbox_are_app_scoped():
    # Two apps in ONE workspace must route to different connections: the key is
    # api_app_id:team_id, not team_id alone.
    p = SlackProvider(apps=_pool_apps(2))
    import json as _j
    payload = _j.dumps({"api_app_id": "A1", "team_id": "TW", "event": {}}).encode()
    assert p.route_key(payload) == "A1:TW"
    from comm_gateway.providers.slack import parse_event
    msgs = parse_event({"api_app_id": "A1", "team_id": "TW", "event_id": "e1",
                        "event": {"type": "message", "channel": "C", "ts": "1.0",
                                  "user": "U", "text": "hi"}})
    assert msgs[0].provider_inbox_id == "A1:TW"


def test_exchange_code_resource_id_is_app_scoped():
    # Same workspace, two different pool apps -> two distinct resource ids, so the
    # two developers' agents coexist instead of clobbering one another.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "ok": True, "access_token": "xoxb-x", "app_id": "A1",
            "team": {"id": "TW", "name": "Shared WS"}})

    p = SlackProvider(apps=_pool_apps(2))
    p._client = httpx.Client(base_url="https://slack.com/api",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    res = p.exchange_code("code", "https://gw.test/cb",
                          app={"slack_client_id": "cid1", "slack_client_secret": "cs1"})
    assert res["provider_resource_id"] == "A1:TW"


def test_pool_assigns_distinct_apps_to_distinct_developers():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    provider = SlackProvider(apps=_pool_apps(4))
    app = create_app(settings, providers={provider.name: provider})
    anon = TestClient(app)

    def _install_and_index(api_key):
        sc = TestClient(app, headers={"Authorization": f"Bearer {api_key}"})
        conn = sc.post("/v1/connections/slack/install", json={}).json()
        with app.state.session_factory() as session:
            from comm_gateway.models import Connection
            return conn, session.get(Connection, conn["id"]).provider_credentials["pool_index"]

    key_a = anon.post("/v1/projects/sandbox", json={"name": "dev-a"}).json()["api_key"]
    key_b = anon.post("/v1/projects/sandbox", json={"name": "dev-b"}).json()["api_key"]

    conn_a, idx_a = _install_and_index(key_a)
    conn_b, idx_b = _install_and_index(key_b)
    # distinct developers -> distinct pool apps (coalition case: coexist in one WS)
    assert idx_a == 0 and idx_b == 1
    # each install's authorize_url carries its own app's client id
    assert "cid0" in conn_a["authorize_url"] and "cid1" in conn_b["authorize_url"]

    # a developer re-installing keeps their assigned app (idempotent, no clobber)
    _, idx_a2 = _install_and_index(key_a)
    assert idx_a2 == 0


def test_send_posts_under_custom_name_and_icon():
    # real provider: chat.postMessage carries username + icon_url from credentials
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _j
        sent.update(_j.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    p = SlackProvider()
    p._client = httpx.Client(base_url="https://slack.com/api",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    from comm_gateway.providers.base import OutboundMessage
    p.send("T1", OutboundMessage(text="hello", to=("C9",)),
           credentials={"bot_token": "xoxb", "display_name": "Acme Support",
                        "icon_url": "https://x/i.png"})
    assert sent["username"] == "Acme Support"
    assert sent["icon_url"] == "https://x/i.png"
    assert sent["channel"] == "C9"
