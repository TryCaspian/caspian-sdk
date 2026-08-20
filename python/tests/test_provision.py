"""Tests for provisioning — channels.add behavior."""

from __future__ import annotations

import pytest

from caspian.provision import Channels, ProvisionError, Via


class TestChannelsAdd:
    def test_hosted_is_default(self) -> None:
        channels = Channels()
        conn = channels.add("email")
        assert conn.via == Via.HOSTED
        assert conn.channel == "email"
        assert conn.inbound_owner == "gateway"

    def test_self_host_requires_token(self) -> None:
        channels = Channels()
        with pytest.raises(ProvisionError, match="bot_token"):
            channels.add("telegram", via="self-host")

    def test_self_host_with_token_succeeds(self) -> None:
        channels = Channels()
        conn = channels.add(
            "telegram",
            via="self-host",
            bot_token="123:ABC",
            webhook_url="https://example.com/webhook",
        )
        assert conn.via == Via.SELF_HOST
        assert conn.config["bot_token"] == "123:ABC"

    def test_list_connections(self) -> None:
        channels = Channels()
        channels.add("email")
        channels.add("discord")
        assert len(channels.list()) == 2
