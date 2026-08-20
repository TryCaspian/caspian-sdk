"""Error ADT — closed union, exhaustive match. No raise across core boundary."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class DecodeError(BaseModel):
    """Failed to parse inbound bytes into an Event."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["DecodeError"] = "DecodeError"
    reason: str


class AdapterError(BaseModel):
    """Adapter failed to execute a command."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["AdapterError"] = "AdapterError"
    reason: str
    command_tag: str = ""


class OverlapFull(BaseModel):
    """Overlap queue is at its bound — event was dropped."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["OverlapFull"] = "OverlapFull"
    thread_id: str
    bound: int


class ProvisionError(BaseModel):
    """Provisioning failed (missing secret, invalid token, etc.)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["ProvisionError"] = "ProvisionError"
    reason: str


class AuthRequired(BaseModel):
    """Hosted gateway rejected credentials or none were provided (401/403)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["AuthRequired"] = "AuthRequired"
    reason: str = ""


class AccountRequired(BaseModel):
    """Hosted gateway requires an account/project that does not exist yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["AccountRequired"] = "AccountRequired"
    reason: str = ""


class InsufficientCredit(BaseModel):
    """Hosted gateway rejected the call for lack of billing credit (402)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["InsufficientCredit"] = "InsufficientCredit"
    reason: str = ""
    balance_cents: int = 0


class RateLimited(BaseModel):
    """A platform or the gateway rate-limited the call (429)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["RateLimited"] = "RateLimited"
    reason: str = ""
    retry_after_seconds: float = 0.0


class GatewayError(BaseModel):
    """Hosted gateway returned an unexpected error (5xx / unclassified 4xx)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tag: Literal["GatewayError"] = "GatewayError"
    reason: str
    status_code: int = 0


CaspianError = Annotated[
    DecodeError
    | AdapterError
    | OverlapFull
    | ProvisionError
    | AuthRequired
    | AccountRequired
    | InsufficientCredit
    | RateLimited
    | GatewayError,
    Field(discriminator="tag"),
]
