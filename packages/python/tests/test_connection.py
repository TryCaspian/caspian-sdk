"""One domain Connection: add(), adapters, and hosted decode share a type."""

from __future__ import annotations

from caspian.connection import Connection, Via
from caspian.facade.caspian import Caspian
from caspian.hosted.client import FakeGatewayClient
from caspian.hosted.provisioning import HostedProvisioning


def test_add_and_connection_for_are_the_same_record() -> None:
    cx = Caspian(dispatch=False)
    result = cx.channels.add("slack", via="self-host", bot_token="xoxb-1")
    assert result.is_ok
    added = result.value
    got = cx.channels.connection_for("slack")
    assert added is got
    assert isinstance(added, Connection)
    assert added.channel == "slack"
    assert added.via == Via.SELF_HOST
    assert added.inbound_owner == "local"
    assert added.config["bot_token"] == "xoxb-1"
    assert cx.channels.list()[0] is added


def test_hosted_decode_is_domain_connection() -> None:
    client = FakeGatewayClient()
    client.queue_ok({})
    client.queue_ok(
        {
            "id": "conn_1",
            "channel": "discord",
            "status": "pending",
            "address": "@bot",
            "authorize_url": "https://discord/oauth",
        }
    )
    result = HostedProvisioning(client).add_connection("discord", {"scope": "bot"})
    assert result.is_ok
    conn = result.value
    assert isinstance(conn, Connection)
    assert conn.id == "conn_1"
    assert conn.channel == "discord"
    assert conn.status == "pending"
    assert conn.address == "@bot"
    assert conn.authorize_url == "https://discord/oauth"
    assert conn.via == Via.HOSTED
    assert conn.inbound_owner == "gateway"


def test_hosted_add_overlays_gateway_id_onto_the_same_record() -> None:
    client = FakeGatewayClient()
    client.queue_ok({})  # list existing
    client.queue_ok(
        {
            "id": "gw_99",
            "channel": "email",
            "status": "active",
            "address": "bot@example.com",
        }
    )
    cx = Caspian(dispatch=False, gateway_client=client)
    result = cx.channels.add("email")
    assert result.is_ok
    added = result.value
    assert added is cx.channels.connection_for("email")
    assert added.id == "gw_99"
    assert added.address == "bot@example.com"
    assert added.status == "active"
    assert added.via == Via.HOSTED
    assert added.inbound_owner == "gateway"
