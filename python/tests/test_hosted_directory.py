"""Tests for HostedDirectory: agents/customers list decoding, behavior text."""

from __future__ import annotations

from caspian.core.ports import Result
from caspian.hosted.client import FakeGatewayClient, GatewayResponse
from caspian.hosted.directory import Agent, Customer, HostedDirectory


def test_agents_decodes_list() -> None:
    client = FakeGatewayClient()
    client.queue_ok(
        {"agents": [{"id": "a1", "name": "Ada"}, {"id": "a2", "name": "Bob"}]}
    )
    result = HostedDirectory(client).agents()

    assert result.is_ok
    assert isinstance(result.value[0], Agent)
    assert [a.name for a in result.value] == ["Ada", "Bob"]
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/agents"


def test_agents_empty_when_missing() -> None:
    client = FakeGatewayClient()
    client.queue_ok({})
    result = HostedDirectory(client).agents()

    assert result.is_ok
    assert result.value == []


def test_customers_decodes_list_with_defaults() -> None:
    client = FakeGatewayClient()
    client.queue_ok(
        {
            "customers": [
                {"id": "c1", "handle": "@x", "channel": "telegram"},
                {"id": "c2"},
            ]
        }
    )
    result = HostedDirectory(client).customers()

    assert result.is_ok
    assert isinstance(result.value[0], Customer)
    assert result.value[0].handle == "@x"
    assert result.value[0].channel == "telegram"
    assert result.value[1].handle == ""
    assert result.value[1].channel == ""
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/customers"


def test_behavior_prompt_returns_text() -> None:
    client = FakeGatewayClient()
    client.queue(
        Result.ok(GatewayResponse(status_code=200, text_body="Be kind and concise."))
    )
    result = HostedDirectory(client).behavior_prompt()

    assert result.is_ok
    assert result.value == "Be kind and concise."
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/behavior-prompt"
    assert req.text_response is True
