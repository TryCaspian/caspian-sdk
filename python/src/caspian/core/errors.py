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


CaspianError = Annotated[
    DecodeError | AdapterError | OverlapFull | ProvisionError,
    Field(discriminator="tag"),
]
