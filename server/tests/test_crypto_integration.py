"""Credentials are stored encrypted end-to-end when a key is configured."""

from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.main import create_app
from comm_gateway.models import Connection
from comm_gateway.providers.fakes.fake_telegram import FakeTelegramProvider
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

KEY = "comm_enc_key"


def _app():
    settings = Settings(
        database_url="sqlite://",
        bootstrap_api_key=KEY,
        inline_worker=False,
        credentials_key=Fernet.generate_key().decode(),
    )
    p = FakeTelegramProvider()
    return create_app(settings, providers={p.name: p})


def test_telegram_credentials_encrypted_at_rest():
    app = _app()
    client = TestClient(app, headers={"Authorization": f"Bearer {KEY}"})
    conn = client.post("/v1/connections/telegram", json={"bot_token": "123:SECRETdata"}).json()
    run_pending_jobs(app.state.session_factory, app.state.providers)

    with app.state.session_factory() as session:
        raw = session.get(Connection, conn["id"]).provider_credentials
    # stored column is a Fernet envelope, not the plaintext token
    assert "__enc__" in raw
    assert "SECRETdata" not in str(raw)

    # but the connection still works: inbound routes + reply sends (decrypts live)
    provider = app.state.providers["fake-telegram"]
    inbox = "123"
    from comm_gateway.crypto import read_credentials
    with app.state.session_factory() as session:
        secret = read_credentials(session.get(Connection, conn["id"]))["webhook_secret"]
    from comm_gateway.providers.telegram import SECRET_HEADER
    r = TestClient(app).post(
        f"/internal/providers/fake-telegram/webhooks/{inbox}",
        json=provider.webhook_payload(text="hi"),
        headers={SECRET_HEADER: secret},
    )
    assert r.status_code == 204
