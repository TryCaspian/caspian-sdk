"""Tests for provisioning — channels.add behavior."""

from __future__ import annotations

from caspian.connection import Connection
from caspian.provision import Channels, Via, bot_token_error


class TestChannelsAdd:
    def test_hosted_is_default(self) -> None:
        channels = Channels()
        conn = channels.add("email")
        assert isinstance(conn, Connection)
        assert conn.via == Via.HOSTED
        assert conn.channel == "email"
        assert conn.inbound_owner == "gateway"

    def test_self_host_requires_token(self) -> None:
        channels = Channels()
        got = channels.add("telegram", via="self-host")
        assert isinstance(got, str)
        assert "bot_token" in got

    def test_bot_token_error_matches_add(self) -> None:
        assert bot_token_error("telegram", "hosted", "") is not None
        assert bot_token_error("email", "hosted", "") is None

    def test_self_host_with_token_succeeds(self) -> None:
        channels = Channels()
        conn = channels.add(
            "telegram",
            via="self-host",
            bot_token="123:ABC",
            webhook_url="https://example.com/webhook",
        )
        assert isinstance(conn, Connection)
        assert conn.via == Via.SELF_HOST
        assert conn.config["bot_token"] == "123:ABC"

    def test_list_connections(self) -> None:
        channels = Channels()
        channels.add("email")
        channels.add("discord")
        assert len(channels.list()) == 2
