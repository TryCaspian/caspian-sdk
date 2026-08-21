"""Channel catalog is the one vocabulary: names, inbound, bot-token, capabilities."""

from __future__ import annotations

import pytest

from caspian.adapters import REGISTRY
from caspian.catalog import (
    CHANNELS,
    BotTokenWhen,
    Capability,
    SocketKind,
    capabilities_of,
    needs_bot_token,
    socket_channels,
)
from caspian.core.errors import ProvisionError as CoreProvisionError
from caspian.facade.caspian import Caspian
from caspian.provision import Channels, ProvisionError, Via


def test_registry_is_catalog_names() -> None:
    assert set(REGISTRY) == set(CHANNELS)


def test_socket_listen_is_derived_from_rows() -> None:
    derived = socket_channels()
    assert set(derived) == {
        name for name, row in CHANNELS.items() if row.socket is not None
    }
    assert set(derived) == {"discord", "slack"}
    assert CHANNELS["discord"].socket == SocketKind.DISCORD
    assert CHANNELS["slack"].socket == SocketKind.SLACK
    assert CHANNELS["telegram"].socket is None


def test_telegram_bot_token_is_a_row_not_an_if() -> None:
    assert CHANNELS["telegram"].bot_token == BotTokenWhen.ALWAYS
    assert CHANNELS["email"].bot_token == BotTokenWhen.SELF_HOST
    assert needs_bot_token("telegram", "hosted")
    assert needs_bot_token("telegram", "self-host")
    assert not needs_bot_token("email", "hosted")
    assert needs_bot_token("email", "self-host")
    assert not needs_bot_token("bluesky", "hosted")


def test_provision_uses_catalog_for_bot_token() -> None:
    with pytest.raises(ProvisionError, match="bot_token"):
        Channels().add("telegram")
    conn = Channels().add("email")
    assert conn.via == Via.HOSTED


def test_listen_allowlist_follows_socket_rows() -> None:
    cx = Caspian()
    cx.channels.add("telegram", via="self-host", bot_token="1:A")
    results = cx.listen("telegram")
    assert len(results) == 1 and not results[0].is_ok
    assert isinstance(results[0].error, CoreProvisionError)
    reason = results[0].error.reason
    for name in socket_channels():
        assert name in reason
    assert "telegram" in reason


def test_adapter_capabilities_come_from_catalog() -> None:
    for name, cls in REGISTRY.items():
        assert cls().capabilities() == capabilities_of(name)
    assert Capability.EDIT.value in capabilities_of("telegram")
    assert Capability.EDIT.value not in capabilities_of("email")
