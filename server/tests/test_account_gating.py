"""Free channels need no signup; paid (Caspian-network) channels require credit.

The gate is the prepaid balance, not an account — no dashboard, ever. A legacy
dashboard account's free credit migrates into the project's billing account on
first touch. Also covers the device sign-in carrying over an anonymous project.
"""

import pytest
from comm_gateway.auth import hash_key
from comm_gateway.config import Settings
from comm_gateway.crypto import _encrypt
from comm_gateway.ids import new_id
from comm_gateway.main import create_app
from comm_gateway.models import ApiKey, DashboardAccount, Project
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from comm_gateway.routes.usage import get_or_create_account, project_has_account
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine


@pytest.fixture()
def fk_enforced():
    """Enforce foreign keys on SQLite (default suite doesn't), so project-creation
    paths are exercised the way Postgres enforces them in prod."""
    def _on_connect(dbapi_con, _rec):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    event.listen(Engine, "connect", _on_connect)
    yield
    event.remove(Engine, "connect", _on_connect)


def test_sandbox_and_signup_create_projects_under_fk_enforcement(fk_enforced):
    # Regression: new-project creation must flush the project before its FK
    # children, or Postgres rejects the insert (SQLite normally hides this).
    settings = Settings(
        database_url="sqlite://", bootstrap_api_key="comm_test_key", inline_worker=False,
    )
    email = FakeEmailProvider()
    app = create_app(settings, providers={email.name: email})  # bootstrap runs under FK
    client = TestClient(app)

    r = client.post("/v1/projects/sandbox", json={"name": "x"})
    assert r.status_code == 201

    with app.state.session_factory() as s:
        pid, key = get_or_create_account(s, "new@example.com", settings)
        assert pid and key


def _project_id_for_key(app, api_key: str) -> str:
    with app.state.session_factory() as s:
        row = s.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_key(api_key))
        ).scalar_one()
        return row.project_id


def test_free_channel_needs_no_account(client):
    # Email is a free channel (not in billing.PAID_CHANNELS), so an anonymous
    # key with no credit can connect it.
    r = client.post("/v1/connections/email", json={})
    assert r.status_code == 201


def test_paid_channel_requires_credit(app, client, monkeypatch):
    # Treat email as paid for this test.
    from comm_gateway import billing

    monkeypatch.setattr(billing, "PAID_CHANNELS", ["email"])

    # Anonymous project with zero balance is blocked, and the 402 tells the
    # caller exactly how to fix it (payment_options), no sign-in involved.
    r = client.post("/v1/connections/email", json={})
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["reason"] == "insufficient_credit"
    assert detail["payment_options"][0]["type"] == "dashboard"

    # A dashboard account's credit migrates in and opens the gate.
    project_id = _project_id_for_key(app, "comm_test_key")
    with app.state.session_factory() as s:
        s.add(DashboardAccount(
            email="dev@example.com", project_id=project_id,
            api_key_enc=_encrypt({"api_key": "comm_test_key"}), credit_cents=10000,
        ))
        s.commit()
    r = client.post("/v1/connections/email", json={})
    assert r.status_code == 201


def test_paid_channel_requires_signin_when_enabled(monkeypatch):
    """Model B: with sign-in configured, a paid channel on an account-less project
    returns 401 account_required (+ a device-login path); once the project has an
    account it advances to the credit gate (402, no free credit)."""
    from comm_gateway import billing

    settings = Settings(
        database_url="sqlite://", bootstrap_api_key="comm_test_key", inline_worker=False,
        supabase_url="https://x.supabase.co", supabase_anon_key="anon",
    )
    email = FakeEmailProvider()
    app = create_app(settings, providers={email.name: email})
    client = TestClient(app, headers={"Authorization": "Bearer comm_test_key"})
    monkeypatch.setattr(billing, "PAID_CHANNELS", ["email"])  # treat email as paid here

    # No account yet -> blocked on sign-in, with a machine-actionable login path.
    r = client.post("/v1/connections/email", json={})
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert detail["reason"] == "account_required"
    assert detail["login_options"][0]["start"]["url"].endswith("/v1/auth/device/start")

    # Once signed in (account exists), the account gate passes and we reach the
    # credit gate: no free credit -> 402 insufficient_credit.
    pid = _project_id_for_key(app, "comm_test_key")
    with app.state.session_factory() as s:
        s.add(DashboardAccount(email="dev@example.com", project_id=pid,
                               api_key_enc=_encrypt({"api_key": "comm_test_key"})))
        s.commit()
    r = client.post("/v1/connections/email", json={})
    assert r.status_code == 402
    assert r.json()["detail"]["reason"] == "insufficient_credit"


def test_signin_carries_over_anonymous_project(app):
    settings = app.state.settings
    with app.state.session_factory() as s:
        proj = Project(id=new_id("proj"), name="anon")
        s.add(proj)
        anon_key = "comm_sandbox_carryover"
        s.add(ApiKey(id=new_id("key"), project_id=proj.id, key_hash=hash_key(anon_key)))
        s.commit()
        pid = proj.id
        assert project_has_account(s, pid) is False

        # Signing in with the anonymous key binds THAT project + keeps the key.
        got_pid, got_key = get_or_create_account(
            s, "dev@example.com", settings, link_project_id=pid, link_api_key=anon_key
        )
        assert got_pid == pid
        assert got_key == anon_key
        assert project_has_account(s, pid) is True

        # Same email signs in again -> same project, no duplicate.
        again_pid, _ = get_or_create_account(s, "dev@example.com", settings)
        assert again_pid == pid


def test_dashboard_first_absorbs_sandbox_connections(app):
    """Email already has an account; CLI login with a sandbox key must not orphan
    sandbox connections — absorb them into the account project and return the
    account key (one email = one project)."""
    from comm_gateway.models import Agent, Connection, Customer
    from comm_gateway.routes.usage import absorb_unowned_sandbox

    settings = app.state.settings
    with app.state.session_factory() as s:
        account_pid, account_key = get_or_create_account(s, "dash@example.com", settings)

        sandbox = Project(id=new_id("proj"), name="sandbox")
        s.add(sandbox)
        s.flush()
        sandbox_key = "comm_sandbox_orphan"
        s.add(ApiKey(id=new_id("key"), project_id=sandbox.id, key_hash=hash_key(sandbox_key)))
        cus = Customer(id=new_id("cus"), project_id=sandbox.id, name="default")
        agt = Agent(id=new_id("agt"), project_id=sandbox.id, name="default")
        s.add(cus)
        s.add(agt)
        s.flush()
        s.add(Connection(
            id=new_id("con"), project_id=sandbox.id, customer_id=cus.id, agent_id=agt.id,
            channel="email", status="active", provider="fake", address="bot@test.local",
        ))
        s.commit()
        sandbox_id = sandbox.id

        got_pid, got_key = get_or_create_account(
            s, "dash@example.com", settings,
            link_project_id=sandbox_id, link_api_key=sandbox_key,
        )
        assert got_pid == account_pid
        assert got_key == account_key

        moved = s.execute(
            select(Connection).where(Connection.project_id == account_pid)
        ).scalars().all()
        assert len(moved) == 1
        assert moved[0].address == "bot@test.local"
        assert s.execute(
            select(Connection).where(Connection.project_id == sandbox_id)
        ).scalars().first() is None

        # Idempotent: absorbing again moves nothing.
        again = absorb_unowned_sandbox(s, account_pid, sandbox_id)
        assert again["connections"] == 0


def test_signup_emits_project_created(app, monkeypatch):
    from comm_gateway import analytics as analytics_mod

    events: list[tuple[str, str, dict]] = []

    def _capture(distinct_id, event, properties=None):
        events.append((distinct_id, event, properties or {}))

    monkeypatch.setattr(analytics_mod, "capture", _capture)
    monkeypatch.setattr(analytics_mod, "identify", lambda *a, **k: None)
    monkeypatch.setattr(analytics_mod, "alias", lambda *a, **k: None)

    settings = app.state.settings
    with app.state.session_factory() as s:
        pid, _ = get_or_create_account(s, "new-signup@example.com", settings)

    created = [e for e in events if e[1] == "gateway.project_created"]
    assert len(created) == 1
    assert created[0][0] == pid
    assert created[0][2].get("source") == "signup"
