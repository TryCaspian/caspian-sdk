"""CLI telemetry ingest + connection.failed channel props."""

from comm_gateway.analytics import safe_props
from comm_gateway.auth import hash_key
from comm_gateway.ids import new_id
from comm_gateway.models import ApiKey, Project


def test_cli_telemetry_allowlisted_without_auth(client, monkeypatch):
    from comm_gateway.routes import cli_telemetry as telem

    events = []

    def _capture(distinct_id, event, properties=None):
        events.append((distinct_id, event, properties or {}))

    monkeypatch.setattr(telem, "capture", _capture)
    monkeypatch.setattr(telem, "identify", lambda *a, **k: None)

    # No Authorization — use a bare client so we don't attach the bootstrap key.
    bare = client.app
    from fastapi.testclient import TestClient
    anon = TestClient(bare)

    r = anon.post("/v1/cli/telemetry", json={
        "event": "cli.init_started",
        "distinct_id": "anonymous:abc",
        "properties": {
            "sandbox": False,
            "cli_session_id": "sess1",
            "cli_version": "0.4.0",
            "bot_token": "SHOULD_NOT_FORWARD",
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
    assert props.get("source") == "cli"


def test_cli_telemetry_rejects_unknown_event(client):
    r = client.post("/v1/cli/telemetry", json={
        "event": "cli.hacked",
        "properties": {},
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_cli_telemetry_attaches_project_from_bearer(app, monkeypatch):
    from fastapi.testclient import TestClient
    from comm_gateway.routes import cli_telemetry as telem

    events = []
    monkeypatch.setattr(
        telem, "capture",
        lambda d, e, p=None: events.append((d, e, p or {})),
    )
    monkeypatch.setattr(telem, "identify", lambda *a, **k: None)

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
            "properties": {"channel": "slack"},
        },
    )
    assert r.status_code == 200
    assert events[0][2].get("project_id") == pid
    assert events[0][2].get("channel") == "slack"


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
