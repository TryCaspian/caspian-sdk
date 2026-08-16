"""Hosted mode — the gateway-runner layer.

In hosted mode Caspian's gateway (api.trycaspianai.com) owns platform identity,
webhooks, secrets, and billing. The SDK talks to the gateway's /v1/* API instead
of the platform directly. This is the same `App` run under a different
transport + inbound source — never a second program.

Layering (import-linter enforced):
    core        → nothing (pure)
    hosted      → core + httpx (allowed I/O at the edge)
    facade      → core + adapters + provision + hosted
core must NOT import hosted.
"""

from __future__ import annotations

from caspian.hosted.client import (
    FakeGatewayClient,
    GatewayClient,
    GatewayRequest,
    GatewayResponse,
    HttpGatewayClient,
    classify_status,
)

__all__ = [
    "FakeGatewayClient",
    "GatewayClient",
    "GatewayRequest",
    "GatewayResponse",
    "HttpGatewayClient",
    "classify_status",
]
