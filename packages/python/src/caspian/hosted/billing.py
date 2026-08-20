"""HostedBilling — the hosted-mode billing surface over the gateway.

Each method builds a pure GatewayRequest and sends it through the injected
GatewayClient. On success the JSON body is decoded defensively into a frozen
Pydantic model; on failure the classified CaspianError result is propagated
unchanged. No network happens here — that lives in the GatewayClient.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from caspian.core.ports import Result
from caspian.hosted.client import GatewayClient, GatewayRequest


class Billing(BaseModel):
    """Current billing state for the hosted account."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    balance_cents: int
    currency: str = "usd"
    autopay: bool = False


class BillingLimits(BaseModel):
    """Spend limits configured for the hosted account."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    daily_cents: int


class Usage(BaseModel):
    """Usage totals for the hosted account."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    messages: int = 0
    cents: int = 0


def _decode_billing(body: dict[str, Any]) -> Billing:
    return Billing(
        balance_cents=int(body.get("balance_cents", 0)),
        currency=str(body.get("currency", "usd")),
        autopay=bool(body.get("autopay", False)),
    )


class HostedBilling:
    """Wraps the gateway's /v1/billing and /v1/usage endpoints."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def get(self) -> Result:
        result = self._client.send(GatewayRequest(method="GET", path="/v1/billing"))
        if not result.is_ok:
            return result
        return Result.ok(_decode_billing(result.value.json_body))

    def topup(self, amount_cents: int) -> Result:
        result = self._client.send(
            GatewayRequest(
                method="POST",
                path="/v1/billing/topup",
                json_body={"amount_cents": amount_cents},
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(_decode_billing(result.value.json_body))

    def set_autopay(self, enabled: bool, threshold_cents: int = 0) -> Result:
        result = self._client.send(
            GatewayRequest(
                method="POST",
                path="/v1/billing/autopay",
                json_body={"enabled": enabled, "threshold_cents": threshold_cents},
            )
        )
        if not result.is_ok:
            return result
        return Result.ok(_decode_billing(result.value.json_body))

    def set_limits(self, daily_cents: int) -> Result:
        result = self._client.send(
            GatewayRequest(
                method="POST",
                path="/v1/billing/limits",
                json_body={"daily_cents": daily_cents},
            )
        )
        if not result.is_ok:
            return result
        body = result.value.json_body
        return Result.ok(BillingLimits(daily_cents=int(body.get("daily_cents", 0))))

    def usage(self) -> Result:
        result = self._client.send(GatewayRequest(method="GET", path="/v1/usage"))
        if not result.is_ok:
            return result
        body = result.value.json_body
        return Result.ok(
            Usage(
                messages=int(body.get("messages", 0)),
                cents=int(body.get("cents", 0)),
            )
        )
