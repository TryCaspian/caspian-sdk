"""HostedDirectory — read-only directory of agents, customers, and behavior.

Each method builds a pure GatewayRequest and sends it through the injected
GatewayClient. On success the body is decoded defensively into frozen Pydantic
models (or returned as text); on failure the classified CaspianError result is
propagated unchanged. No network happens here — that lives in the GatewayClient.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from caspian.core.ports import Result
from caspian.hosted.client import GatewayClient, GatewayRequest


class Agent(BaseModel):
    """An agent registered with the hosted account."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    name: str


class Customer(BaseModel):
    """A customer known to the hosted account."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    handle: str = ""
    channel: str = ""


def _items(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = body.get(key, []) if isinstance(body, dict) else body
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


class HostedDirectory:
    """Wraps the gateway's /v1 agents, customers, and behavior-prompt endpoints."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def agents(self) -> Result:
        result = self._client.send(GatewayRequest(method="GET", path="/v1/agents"))
        if not result.is_ok:
            return result
        agents = [
            Agent(id=str(item.get("id", "")), name=str(item.get("name", "")))
            for item in _items(result.value.json_body, "agents")
        ]
        return Result.ok(agents)

    def customers(self) -> Result:
        result = self._client.send(GatewayRequest(method="GET", path="/v1/customers"))
        if not result.is_ok:
            return result
        customers = [
            Customer(
                id=str(item.get("id", "")),
                handle=str(item.get("handle", "")),
                channel=str(item.get("channel", "")),
            )
            for item in _items(result.value.json_body, "customers")
        ]
        return Result.ok(customers)

    def behavior_prompt(self) -> Result:
        result = self._client.send(
            GatewayRequest(
                method="GET",
                path="/v1/behavior-prompt",
                text_response=True,
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(result.value.text_body)
