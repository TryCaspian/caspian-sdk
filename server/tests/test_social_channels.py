"""Discord / Slack / Instagram / Facebook through the full gateway stack."""

import pytest
from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake_social import (
    FakeDiscordProvider,
    FakeFacebookProvider,
    FakeInstagramProvider,
    FakeSlackProvider,
)
from fastapi.testclient import TestClient

API_KEY = "comm_social_key"
DISCORD_TOKEN = "OTk5.abc.def"  # first segment b64 -> "999"


@pytest.fixture()
def social_app():
    settings = Settings(
        database_url="sqlite://",
        bootstrap_api_key=API_KEY,
        inline_worker=False,
        public_base_url="https://gw.test",
    )
    provs = {}
    for P in (FakeDiscordProvider, FakeSlackProvider, FakeInstagramProvider, FakeFacebookProvider):
        p = P()
        provs[p.name] = p
    return create_app(settings, providers=provs)


@pytest.fixture()
def sc(social_app):
    return TestClient(social_app, headers={"Authorization": f"Bearer {API_KEY}"})


def _run(app):
    run_pending_jobs(app.state.session_factory, app.state.providers)


# --- Discord (bot token, scoped webhook) ---

def test_discord_connect_and_reply(social_app, sc):
    conn = sc.post("/v1/connections/discord", json={"bot_token": DISCORD_TOKEN}).json()
    assert conn["status"] == "provisioning"
    _run(social_app)
    conn = sc.get(f"/v1/connections/{conn['id']}").json()
    assert conn["status"] == "active"
    resource = "999"

    provider = social_app.state.providers["fake-discord"]
    anon = TestClient(social_app)
    payload = provider.webhook_payload(channel_id="chan42", text="hey bot")
    r = anon.post(f"/internal/providers/fake-discord/webhooks/{resource}", json=payload)
    assert r.status_code == 204
    _run(social_app)

    events = sc.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hey bot"
    inbound_id = events[-1]["data"]["message"]["id"]

    sc.post(f"/v1/messages/{inbound_id}/reply", json={"text": "hi there"})
    _run(social_app)
    assert provider.replies[-1]["text"] == "hi there"
    assert provider.replies[-1]["channel"] == "chan42"


def test_discord_same_bot_conflicts(social_app, sc):
    sc.post("/v1/connections/discord", json={"bot_token": DISCORD_TOKEN})
    other_key = TestClient(social_app).post("/v1/projects/sandbox", json={}).json()["api_key"]
    other = TestClient(social_app, headers={"Authorization": f"Bearer {other_key}"})
    r = other.post("/v1/connections/discord", json={"bot_token": DISCORD_TOKEN})
    assert r.status_code == 409


# --- Slack (OAuth + Events webhook) ---

def test_slack_oauth_flow_and_reply(social_app, sc):
    conn = sc.post("/v1/connections/slack", json={}).json()
    assert conn["status"] == "pending_oauth"
    assert conn["authorize_url"].startswith("https://slack.com/oauth")

    provider = social_app.state.providers["fake-slack"]
    state = None
    with social_app.state.session_factory() as session:
        from comm_gateway.models import Connection

        c = session.get(Connection, conn["id"])
        state = c.provider_credentials["oauth_state"]

    anon = TestClient(social_app)
    cb = anon.get(
        "/v1/oauth/fake-slack/callback",
        params={"code": "abc", "state": state},
        follow_redirects=True,
    )
    assert cb.status_code == 200 and "Connected" in cb.text
    _run(social_app)
    conn = sc.get(f"/v1/connections/{conn['id']}").json()
    assert conn["status"] == "active"

    # inbound via the global Slack events webhook, routed by team_id
    r = anon.post("/internal/providers/fake-slack/webhooks", json=provider.webhook_payload(
        channel="C99", text="need help"))
    assert r.status_code == 204
    _run(social_app)
    events = sc.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "need help"
    inbound_id = events[-1]["data"]["message"]["id"]
    sc.post(f"/v1/messages/{inbound_id}/reply", json={"text": "on it"})
    _run(social_app)
    assert provider.replies[-1]["text"] == "on it"


def test_slack_url_verification_challenge(social_app):
    anon = TestClient(social_app)
    r = anon.post(
        "/internal/providers/fake-slack/webhooks",
        json={"type": "url_verification", "challenge": "xyz123"},
    )
    assert r.status_code == 200 and r.text == "xyz123"


# --- Instagram / Facebook (Meta webhooks) ---

@pytest.mark.parametrize("channel,provider_name", [
    ("instagram", "fake-instagram"),
    ("facebook", "fake-facebook"),
])
def test_meta_channel_connect_and_reply(social_app, sc, channel, provider_name):
    conn = sc.post(f"/v1/connections/{channel}", json={}).json()
    # meta fakes are config-based (no oauth attr) -> provision immediately
    _run(social_app)
    conn = sc.get(f"/v1/connections/{conn['id']}").json()
    assert conn["status"] == "active"

    provider = social_app.state.providers[provider_name]
    anon = TestClient(social_app)
    r = anon.post(f"/internal/providers/{provider_name}/webhooks",
                  json=provider.webhook_payload(sender="777", text="hello"))
    assert r.status_code == 204
    _run(social_app)
    events = sc.get("/v1/events", params={"type": "message.received"}).json()
    assert events[-1]["data"]["message"]["text"] == "hello"
    inbound_id = events[-1]["data"]["message"]["id"]
    sc.post(f"/v1/messages/{inbound_id}/reply", json={"text": "hi"})
    _run(social_app)
    assert provider.replies[-1]["to"] == "777"


def test_meta_hub_challenge(social_app):
    anon = TestClient(social_app)
    r = anon.get(
        "/internal/providers/fake-instagram/webhooks",
        params={"hub.mode": "subscribe", "hub.challenge": "999", "hub.verify_token": "x"},
    )
    assert r.status_code == 200 and r.text == "999"


def test_all_social_channels_listed(social_app):
    anon = TestClient(social_app, headers={"Authorization": f"Bearer {API_KEY}"})
    channels = {c["channel"] for c in anon.get("/v1/channels").json()}
    assert {"discord", "slack", "instagram", "facebook"} <= channels


# --- Slack token rotation (access + refresh) ---

def _rotating_slack_app():
    from comm_gateway.config import Settings
    from comm_gateway.main import create_app
    from comm_gateway.providers.fakes.fake_social import FakeSlackProvider

    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    # token already expired so the first send must refresh
    provider = FakeSlackProvider(rotating=True, token_ttl=-1000)
    return create_app(settings, providers={provider.name: provider}), provider


def _install_slack(app, client):
    conn = client.post("/v1/connections/slack", json={}).json()
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        state = session.get(Connection, conn["id"]).provider_credentials["oauth_state"]
    TestClient(app).get("/v1/oauth/fake-slack/callback",
                        params={"code": "abc", "state": state}, follow_redirects=True)
    run_pending_jobs(app.state.session_factory, app.state.providers)
    return conn


def test_slack_exchange_stores_refresh_token():
    app, provider = _rotating_slack_app()
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = _install_slack(app, client)
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        creds = session.get(Connection, conn["id"]).provider_credentials
    assert creds["bot_token"].startswith("xoxb-fake-")
    assert creds["refresh_token"].startswith("xoxe-fake-")
    assert "token_expires_at" in creds


def test_slack_refreshes_expired_token_before_send():
    app, provider = _rotating_slack_app()
    client = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = _install_slack(app, client)

    # inbound to reply to
    anon = TestClient(app)
    anon.post("/internal/providers/fake-slack/webhooks",
              json=provider.webhook_payload(channel="C1", text="hi"))
    run_pending_jobs(app.state.session_factory, app.state.providers)
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[-1]["data"]["message"]["id"]

    assert provider.refreshes == 0
    client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "pong"})
    run_pending_jobs(app.state.session_factory, app.state.providers)

    assert provider.refreshes == 1  # expired token was rotated before the send
    assert provider.replies[-1]["text"] == "pong"
    # the rotated (new) refresh token was persisted back onto the connection
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        creds = session.get(Connection, conn["id"]).provider_credentials
    assert "rotated1" in creds["bot_token"]


def test_slack_byo_app_credentials(social_app):
    """A developer brings their own Slack app; the gateway drives its OAuth."""
    from comm_gateway.models import Connection

    client = TestClient(social_app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = client.post("/v1/connections/slack", json={
        "slack_client_id": "111.222",
        "slack_client_secret": "sekret",
        "slack_signing_secret": "signsign",
    }).json()
    assert conn["status"] == "pending_oauth"
    with social_app.state.session_factory() as session:
        creds = session.get(Connection, conn["id"]).provider_credentials
        assert creds["slack_client_id"] == "111.222"
        assert creds["slack_signing_secret"] == "signsign"


def test_slack_byo_requires_all_three(social_app):
    client = TestClient(social_app, headers={"Authorization": f"Bearer {API_KEY}"})
    # partial app creds with no shared app would 422 — but the fake has a shared
    # app, so partial creds fall back to shared (valid). Verify the real gate:
    # a provider with NO shared app + partial creds is rejected. Simulated by
    # clearing the fake's client_id for this check.
    provider = social_app.state.providers["fake-slack"]
    original = provider.client_id
    provider.client_id = ""
    try:
        r = client.post("/v1/connections/slack", json={"slack_client_id": "only-one"})
        assert r.status_code == 422
        assert "slack_client_id" in r.json()["detail"]
    finally:
        provider.client_id = original


def test_discord_webhook_identity(social_app, sc):
    """A per-agent Discord identity via a channel webhook URL, no bot token."""
    wh = "https://discord.com/api/webhooks/998877/tok3n"
    conn = sc.post("/v1/connections/discord", json={
        "webhook_url": wh, "username": "News Agent",
    }).json()
    assert conn["status"] == "provisioning"
    _run(social_app)
    conn = sc.get(f"/v1/connections/{conn['id']}").json()
    assert conn["status"] == "active"
    assert conn["address"] == "News Agent"

    # a bot_token discord connection and a webhook one coexist (different resource)
    provider = social_app.state.providers["fake-discord"]
    assert conn["id"]
    # send via the webhook identity
    from comm_gateway.jobs import enqueue
    # simulate an initiate/send by posting through the send path
    with social_app.state.session_factory() as session:
        from comm_gateway.ids import new_id
        from comm_gateway.models import Connection, Conversation, Message
        c = session.get(Connection, conn["id"])
        conv = Conversation(id=new_id("conv"), project_id=c.project_id, connection_id=c.id,
                            provider_thread_id="chan1")
        session.add(conv)
        m = Message(id=new_id("msg"), project_id=c.project_id, conversation_id=conv.id,
                    connection_id=c.id, channel="discord", direction="outbound", status="queued",
                    text="hello from the agent")
        session.add(m)
        enqueue(session, "send_message", {"message_id": m.id})
        session.commit()
    _run(social_app)
    assert provider.sent[-1]["username"] == "News Agent"
    assert provider.sent[-1]["text"] == "hello from the agent"


def test_discord_requires_an_identity(social_app, sc):
    r = sc.post("/v1/connections/discord", json={})
    assert r.status_code == 422
    assert "bot_token or a webhook_url" in r.json()["detail"]
