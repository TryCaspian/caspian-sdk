"""GatewayClient — the one place hosted-mode HTTP happens.

A `GatewayClient` sends a `GatewayRequest` (pure data: method, path, json) to the
Caspian gateway and returns a `GatewayResponse` or a classified `CaspianError`.
Everything above this (transport, provisioning, billing) builds request-data and
interprets responses; only this layer touches the network. A `FakeGatewayClient`
lets every hosted unit test run without a network (sdk-reliability: one effect
boundary with a symmetric test double).
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from caspian.core.errors import (
    AccountRequired,
    AuthRequired,
    CaspianError,
    GatewayError,
    InsufficientCredit,
    RateLimited,
)
from caspian.core.ports import Result

DEFAULT_BASE_URL = "https://api.trycaspianai.com"


class GatewayRequest(BaseModel):
    """A pure description of a call to the gateway. Built by transport/provisioning."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    method: str = "POST"
    path: str
    json_body: dict[str, Any] | None = None
    params: dict[str, str] = Field(default_factory=dict)
    text_response: bool = False


class GatewayResponse(BaseModel):
    """A decoded gateway response."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status_code: int
    json_body: dict[str, Any] = Field(default_factory=dict)
    text_body: str = ""


def classify_status(
    status_code: int, detail: str = "", *, retry_after: float = 0.0
) -> CaspianError:
    """Map an HTTP status to a closed-union CaspianError constructor.

    401/403 → AuthRequired, 402 → InsufficientCredit, 404-account → AccountRequired,
    429 → RateLimited, else → GatewayError. Callers pattern-match exhaustively.
    """
    if status_code in (401, 403):
        return AuthRequired(reason=detail or f"HTTP {status_code}")
    if status_code == 402:
        return InsufficientCredit(reason=detail or "insufficient credit")
    if status_code == 429:
        return RateLimited(reason=detail or "rate limited", retry_after_seconds=retry_after)
    if status_code == 404 and "account" in detail.lower():
        return AccountRequired(reason=detail)
    return GatewayError(reason=detail or f"HTTP {status_code}", status_code=status_code)


class GatewayClient(Protocol):
    """Port: send a GatewayRequest, get a Result[GatewayResponse, CaspianError]."""

    def send(self, request: GatewayRequest) -> Result: ...


class HttpGatewayClient:
    """Production GatewayClient over httpx. The only hosted component that does I/O."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def send(self, request: GatewayRequest) -> Result:
        import httpx

        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}{request.path}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.request(
                    request.method,
                    url,
                    json=request.json_body,
                    params=request.params or None,
                    headers=headers,
                )
        except httpx.HTTPError as e:
            return Result.err(GatewayError(reason=f"HTTP error: {e}"))

        if resp.status_code >= 400:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After", ""))
            return Result.err(
                classify_status(resp.status_code, resp.text[:300], retry_after=retry_after)
            )

        if request.text_response:
            return Result.ok(GatewayResponse(status_code=resp.status_code, text_body=resp.text))

        body: dict[str, Any] = {}
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}
        return Result.ok(GatewayResponse(status_code=resp.status_code, json_body=body))


def _parse_retry_after(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class FakeGatewayClient:
    """Test double: records requests, returns queued responses. No network."""

    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []
        self._responses: list[Result] = []
        self.default_response: Result = Result.ok(
            GatewayResponse(status_code=200, json_body={})
        )

    def queue(self, result: Result) -> None:
        """Queue the next Result to return (FIFO)."""
        self._responses.append(result)

    def queue_ok(self, json_body: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self._responses.append(
            Result.ok(GatewayResponse(status_code=status_code, json_body=json_body or {}))
        )

    def queue_status(self, status_code: int, detail: str = "") -> None:
        self._responses.append(Result.err(classify_status(status_code, detail)))

    def send(self, request: GatewayRequest) -> Result:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return self.default_response
