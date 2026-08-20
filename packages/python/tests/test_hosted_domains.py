"""Tests for HostedDomains: list/add/get decoding and text zone-file retrieval."""

from __future__ import annotations

from caspian.core.ports import Result
from caspian.hosted.client import FakeGatewayClient, GatewayResponse
from caspian.hosted.domains import Domain, HostedDomains


def test_list_decodes_domains_key() -> None:
    client = FakeGatewayClient()
    client.queue_ok(
        {
            "domains": [
                {"id": "d1", "name": "a.com", "status": "active", "verified": True},
                {"id": "d2", "name": "b.com", "status": "pending"},
            ]
        }
    )
    result = HostedDomains(client).list()

    assert result.is_ok
    assert [d.id for d in result.value] == ["d1", "d2"]
    assert isinstance(result.value[0], Domain)
    assert result.value[0].verified is True
    assert result.value[1].verified is False
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/domains"


def test_list_empty_when_missing() -> None:
    client = FakeGatewayClient()
    client.queue_ok({})
    result = HostedDomains(client).list()

    assert result.is_ok
    assert result.value == []


def test_add_posts_name_and_decodes() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"id": "d9", "name": "new.com", "status": "pending"})
    result = HostedDomains(client).add("new.com")

    assert result.is_ok
    assert result.value.id == "d9"
    assert result.value.name == "new.com"
    req = client.requests[-1]
    assert req.method == "POST"
    assert req.path == "/v1/domains"
    assert req.json_body == {"name": "new.com"}


def test_get_hits_domain_path() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"id": "d3", "name": "c.com", "status": "active", "verified": True})
    result = HostedDomains(client).get("d3")

    assert result.is_ok
    assert result.value.id == "d3"
    assert result.value.verified is True
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/domains/d3"


def test_zone_file_uses_text_response() -> None:
    client = FakeGatewayClient()
    client.queue(
        Result.ok(GatewayResponse(status_code=200, text_body="; zone file\nA 1.2.3.4"))
    )
    result = HostedDomains(client).zone_file("d3")

    assert result.is_ok
    assert result.value == "; zone file\nA 1.2.3.4"
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/domains/d3/zone-file"
    assert req.text_response is True
