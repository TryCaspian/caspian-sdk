import pytest
from comm_gateway.config import Settings
from comm_gateway.main import create_app
from comm_gateway.providers.fakes.fake import FakeEmailProvider
from comm_gateway.providers.fakes.fake_telegram import FakeTelegramProvider
from comm_gateway.providers.fakes.fake_zulip import FakeZulipProvider
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


@pytest.fixture()
def app():
    settings = Settings(
        database_url="sqlite://",
        provider="fake",
        bootstrap_api_key=API_KEY,
        inline_worker=False,
    )
    email = FakeEmailProvider()
    telegram = FakeTelegramProvider()
    zulip = FakeZulipProvider()
    return create_app(
        settings,
        providers={email.name: email, telegram.name: telegram, zulip.name: zulip},
    )


@pytest.fixture()
def client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


@pytest.fixture()
def run_jobs(app):
    from comm_gateway.jobs import run_pending_jobs

    def _run() -> int:
        return run_pending_jobs(app.state.session_factory, app.state.providers)

    return _run
