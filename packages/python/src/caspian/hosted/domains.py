"""HostedDomains — the hosted-mode custom-domain surface over the gateway.

Each method builds a pure GatewayRequest and sends it through the injected
GatewayClient. On success the JSON body is decoded defensively into frozen
Pydantic models; on failure the classified CaspianError result is propagated
unchanged. No network happens here — that lives in the GatewayClient.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from caspian.core.ports import Result
from caspian.hosted.client import GatewayClient, GatewayRequest


class Domain(BaseModel):
    """A custom domain registered with the hosted account."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    name: str
    status: str
    verified: bool = False


def _decode_domain(body: dict[str, Any]) -> Domain:
    return Domain(
        id=str(body.get("id", "")),
        name=str(body.get("name", "")),
        status=str(body.get("status", "")),
        verified=bool(body.get("verified", False)),
    )


class HostedDomains:
    """Wraps the gateway's /v1/domains endpoints."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def list(self) -> Result:
        result = self._client.send(GatewayRequest(method="GET", path="/v1/domains"))
        if not result.is_ok:
            return result
        body = result.value.json_body
        raw = body.get("domains", []) if isinstance(body, dict) else body
        if not isinstance(raw, list):
            raw = []
        domains = [_decode_domain(item) for item in raw if isinstance(item, dict)]
        return Result.ok(domains)

    def add(self, name: str) -> Result:
        result = self._client.send(
            GatewayRequest(
                method="POST",
                path="/v1/domains",
                json_body={"name": name},
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(_decode_domain(result.value.json_body))

    def get(self, domain_id: str) -> Result:
        result = self._client.send(
            GatewayRequest(method="GET", path=f"/v1/domains/{domain_id}")
        )
        if not result.is_ok:
            return result
        return Result.ok(_decode_domain(result.value.json_body))

    def zone_file(self, domain_id: str) -> Result:
        result = self._client.send(
            GatewayRequest(
                method="GET",
                path=f"/v1/domains/{domain_id}/zone-file",
                text_response=True,
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(result.value.text_body)
