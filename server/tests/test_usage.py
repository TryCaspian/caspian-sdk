"""Dashboard: /v1/usage auth gating + onboarding (project/key provisioning)."""

from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from comm_gateway.routes.usage import compute_usage, get_or_create_account
from fastapi.testclient import TestClient


def _app(**over):
    settings = Settings(database_url="sqlite://", bootstrap_api_key="k",
                        inline_worker=False, **over)
    return create_app(settings, providers={"fake": FakeEmailProvider()})


def test_usage_requires_bearer_token():
    assert TestClient(_app()).get("/v1/usage").status_code == 401


def test_usage_503_when_supabase_not_configured():
    r = TestClient(_app()).get("/v1/usage", headers={"Authorization": "Bearer t"})
    assert r.status_code == 503


def test_onboarding_provisions_project_and_key_idempotently():
    app = _app()
    sf = app.state.session_factory
    with sf() as s:
        pid1, key1 = get_or_create_account(s, "dev@example.com", app.state.settings)
    assert pid1.startswith("proj_") and key1.startswith("comm_")
    # a second sign-in returns the SAME project + key (one developer, one project)
    with sf() as s:
        pid2, key2 = get_or_create_account(s, "dev@example.com", app.state.settings)
    assert (pid2, key2) == (pid1, key1)
    # a different developer gets a different project
    with sf() as s:
        pid3, _ = get_or_create_account(s, "other@example.com", app.state.settings)
    assert pid3 != pid1


def test_compute_usage_empty_project_is_all_zero():
    app = _app()
    with app.state.session_factory() as s:
        pid, _ = get_or_create_account(s, "z@example.com", app.state.settings)
        u = compute_usage(s, pid)
    assert u["bots"] == 0 and u["messages_total"] == 0 and u["cost_total"] == 0
    assert u["platforms"] == [] and u["by_channel"] == []
