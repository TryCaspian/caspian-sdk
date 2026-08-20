"""Tests for HostedProvisioning against a FakeGatewayClient (no network)."""

from __future__ import annotations

from caspian.hosted.client import FakeGatewayClient
from caspian.hosted.provisioning import (
    Channels,
    DeviceStart,
    DeviceToken,
    HostedConnection,
    HostedProvisioning,
    InstallLink,
    WhatsAppOnboarding,
)


def _prov() -> tuple[HostedProvisioning, FakeGatewayClient]:
    client = FakeGatewayClient()
    return HostedProvisioning(client), client


class TestDevice:
    def test_device_start_decodes_fields(self) -> None:
        prov, client = _prov()
        client.queue_ok(
            {
                "device_code": "dev_123",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://caspian/activate",
                "interval": 7,
                "expires_in": 900,
            }
        )
        result = prov.device_start()
        assert result.is_ok
        model = result.value
        assert isinstance(model, DeviceStart)
        assert model.device_code == "dev_123"
        assert model.user_code == "WXYZ-1234"
        assert model.verification_uri == "https://caspian/activate"
        assert model.interval == 7
        assert model.expires_in == 900
        assert client.requests[-1].method == "POST"
        assert client.requests[-1].path == "/v1/auth/device/start"

    def test_device_token_returns_api_key(self) -> None:
        prov, client = _prov()
        client.queue_ok({"api_key": "sk_live_abc", "project_id": "proj_1"})
        result = prov.device_token("dev_123")
        assert result.is_ok
        model = result.value
        assert isinstance(model, DeviceToken)
        assert model.api_key == "sk_live_abc"
        assert model.project_id == "proj_1"
        assert client.requests[-1].path == "/v1/auth/device/token"
        assert client.requests[-1].json_body == {"device_code": "dev_123"}

    def test_device_token_pending_propagates_auth_required(self) -> None:
        prov, client = _prov()
        client.queue_status(403, "authorization_pending")
        result = prov.device_token("dev_123")
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AuthRequired"


class TestConnections:
    def test_add_connection_posts_and_decodes_authorize_url(self) -> None:
        prov, client = _prov()
        # add_connection reuses an existing connection, so it looks first.
        client.queue_ok({})
        client.queue_ok(
            {
                "id": "conn_1",
                "channel": "discord",
                "status": "pending",
                "authorize_url": "https://discord/oauth",
            }
        )
        result = prov.add_connection("discord", {"scope": "bot"})
        assert result.is_ok
        model = result.value
        assert isinstance(model, HostedConnection)
        assert model.authorize_url == "https://discord/oauth"
        assert client.requests[-1].method == "POST"
        assert client.requests[-1].path == "/v1/connections/discord"
        assert client.requests[-1].json_body == {"scope": "bot"}

    def test_add_connection_defaults_options(self) -> None:
        prov, client = _prov()
        client.queue_ok({"id": "conn_1", "channel": "slack", "status": "ok"})
        result = prov.add_connection("slack")
        assert result.is_ok
        assert client.requests[-1].json_body == {}

    def test_get_connection_uses_get(self) -> None:
        prov, client = _prov()
        client.queue_ok({"id": "conn_9", "channel": "slack", "status": "active"})
        result = prov.get_connection("conn_9")
        assert result.is_ok
        assert result.value.status == "active"
        assert client.requests[-1].method == "GET"
        assert client.requests[-1].path == "/v1/connections/conn_9"

    def test_update_connection_uses_patch(self) -> None:
        prov, client = _prov()
        client.queue_ok({"id": "conn_9", "channel": "slack", "status": "paused"})
        result = prov.update_connection("conn_9", {"status": "paused"})
        assert result.is_ok
        assert client.requests[-1].method == "PATCH"
        assert client.requests[-1].path == "/v1/connections/conn_9"
        assert client.requests[-1].json_body == {"status": "paused"}

    def test_delete_connection_uses_delete(self) -> None:
        prov, client = _prov()
        client.queue_ok({})
        result = prov.delete_connection("conn_9")
        assert result.is_ok
        assert client.requests[-1].method == "DELETE"
        assert client.requests[-1].path == "/v1/connections/conn_9"

    def test_initiate_posts_conversation(self) -> None:
        prov, client = _prov()
        client.queue_ok({"message_id": "m1"})
        result = prov.initiate("conn_9", "conv_1", "hello")
        assert result.is_ok
        assert client.requests[-1].method == "POST"
        assert client.requests[-1].path == "/v1/connections/conn_9/initiate"
        assert client.requests[-1].json_body == {"conversation": "conv_1", "text": "hello"}


class TestChannels:
    def test_list_channels_wraps_list(self) -> None:
        prov, client = _prov()
        client.queue_ok({"channels": ["discord", "slack", "x"]})
        result = prov.list_channels()
        assert result.is_ok
        model = result.value
        assert isinstance(model, Channels)
        assert model.channels == ("discord", "slack", "x")
        assert client.requests[-1].method == "GET"
        assert client.requests[-1].path == "/v1/channels"

    def test_list_channels_defensive_when_absent(self) -> None:
        prov, client = _prov()
        client.queue_ok({})
        result = prov.list_channels()
        assert result.is_ok
        assert result.value.channels == ()

    def test_channel_guide_uses_text_response(self) -> None:
        prov, client = _prov()
        from caspian.core.ports import Result
        from caspian.hosted.client import GatewayResponse

        client.queue(
            Result.ok(GatewayResponse(status_code=200, text_body="# Discord guide"))
        )
        result = prov.channel_guide("discord")
        assert result.is_ok
        assert result.value == "# Discord guide"
        assert client.requests[-1].method == "GET"
        assert client.requests[-1].path == "/v1/channels/discord/guide"
        assert client.requests[-1].text_response is True


class TestOAuth:
    def test_install_url_decodes_authorize_url(self) -> None:
        prov, client = _prov()
        client.queue_ok({"authorize_url": "https://slack/oauth/authorize"})
        result = prov.install_url("slack")
        assert result.is_ok
        model = result.value
        assert isinstance(model, InstallLink)
        assert model.authorize_url == "https://slack/oauth/authorize"
        assert client.requests[-1].method == "POST"
        assert client.requests[-1].path == "/v1/connections/slack/install"

    def test_whatsapp_onboarding_decodes_session(self) -> None:
        prov, client = _prov()
        client.queue_ok(
            {"session_id": "wa_sess_1", "url": "https://wa/signup", "expires_in": 600}
        )
        result = prov.whatsapp_onboarding()
        assert result.is_ok
        model = result.value
        assert isinstance(model, WhatsAppOnboarding)
        assert model.session_id == "wa_sess_1"
        assert model.url == "https://wa/signup"
        assert model.expires_in == 600
        assert client.requests[-1].path == "/v1/connections/whatsapp/onboarding-session"


class TestErrorPropagation:
    def test_insufficient_credit_propagates(self) -> None:
        prov, client = _prov()
        client.queue_ok({})  # the existing-connection lookup
        client.queue_status(402, "insufficient credit")
        result = prov.add_connection("discord")
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "InsufficientCredit"

    def test_insufficient_credit_propagates_on_device_start(self) -> None:
        prov, client = _prov()
        client.queue_status(402)
        result = prov.device_start()
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "InsufficientCredit"
