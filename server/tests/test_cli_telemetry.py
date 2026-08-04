"""CLI telemetry ingest + connection.failed channel props."""

from comm_gateway.analytics import safe_props
from comm_gateway.auth import hash_key
from comm_gateway.ids import new_id
from comm_gateway.models import ApiKey, Project
from comm_gateway.routes import cli_telemetry as telem
from fastapi.testclient import TestClient


def test_cli_telemetry_allowlisted_without_auth(client, monkeypatch):
    events = []

    def _capture(distinct_id, event, properties=None):
        events.append((distinct_id, event, properties or {}))

    monkeypatch.setattr(telem, "capture", _capture)
    monkeypatch.setattr(telem, "identify", lambda *a, **k: None)

    # No Authorization — use a bare client so we don't attach the bootstrap key.
    anon = TestClient(client.app)

    r = anon.post("/v1/cli/telemetry", json={
        "event": "cli.init_started",
        "distinct_id": "anonymous:abc",
        "properties": {
            "sandbox": False,
            "cli_session_id": "sess1",
            "cli_version": "0.4.0",
            "bot_token": "SHOULD_NOT_FORWARD",
            "project_id": "proj_spoofed",
            "email": "spoof@example.com",
        },
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert len(events) == 1
    distinct, event, props = events[0]
    assert event == "cli.init_started"
    assert distinct == "anonymous:abc"
    assert props.get("sandbox") is False
    assert "bot_token" not in props
    assert "project_id" not in props
    assert "email" not in props
    assert props.get("source") == "cli"


def test_cli_telemetry_rejects_unknown_event(client):
    r = client.post("/v1/cli/telemetry", json={
        "event": "cli.hacked",
        "properties": {},
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_cli_telemetry_attaches_project_from_bearer(app, monkeypatch):
    events = []
    identifies = []
    monkeypatch.setattr(
        telem, "capture",
        lambda d, e, p=None: events.append((d, e, p or {})),
    )
    monkeypatch.setattr(
        telem, "identify",
        lambda d, p=None: identifies.append((d, p or {})),
    )

    key = "comm_cli_telem_test"
    with app.state.session_factory() as s:
        pid = new_id("proj")
        s.add(Project(id=pid, name="telem"))
        s.flush()
        s.add(ApiKey(id=new_id("key"), project_id=pid, key_hash=hash_key(key)))
        s.commit()

    r = TestClient(app).post(
        "/v1/cli/telemetry",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "event": "cli.connect_started",
            "distinct_id": "anonymous:x",
            "properties": {
                "channel": "slack",
                "email": "dev@example.com",
                "project_id": "proj_spoofed",
            },
        },
    )
    assert r.status_code == 200
    assert events[0][0] == "dev@example.com"
    assert events[0][2].get("project_id") == pid
    assert events[0][2].get("channel") == "slack"
    assert identifies == [("dev@example.com", {"email": "dev@example.com", "project_id": pid})]


def test_cli_telemetry_unauth_rate_limit(client, monkeypatch):
    monkeypatch.setattr(telem, "_UNAUTH_LIMIT", 2)
    monkeypatch.setattr(telem, "capture", lambda *a, **k: None)
    monkeypatch.setattr(telem, "identify", lambda *a, **k: None)
    with telem._unauth_lock:
        telem._unauth_hits.clear()

    anon = TestClient(client.app)
    body = {"event": "cli.session_started", "properties": {"machine_id": "m1"}}
    assert anon.post("/v1/cli/telemetry", json=body).json()["ok"] is True
    assert anon.post("/v1/cli/telemetry", json=body).json()["ok"] is True
    limited = anon.post("/v1/cli/telemetry", json=body).json()
    assert limited == {"ok": False, "error": "rate_limited"}


def test_safe_props_extracts_channel_from_connection():
    props = safe_props("connection.failed", {
        "connection": {"channel": "slack", "provider": "slack", "status": "failed"},
    })
    assert props["channel"] == "slack"
    assert props["provider"] == "slack"


def test_safe_props_prefers_top_level_channel():
    props = safe_props("connection.failed", {
        "channel": "email",
        "connection": {"channel": "slack", "provider": "slack"},
    })
    assert props["channel"] == "email"
