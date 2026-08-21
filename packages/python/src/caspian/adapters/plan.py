"""Planned platform I/O. Adapters describe calls; transports dispatch them.

Typed here — not on AdapterPort — so core never sees Slack's URL.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from caspian.core.ports import Sent


class HttpJsonCall(TypedDict, total=False):
    transport: Literal["http_json"]
    method: str
    url: str
    json: dict[str, Any]
    headers: dict[str, str]
    native: str


class HttpFormCall(TypedDict, total=False):
    transport: Literal["http_form"]
    method: str
    url: str
    form: dict[str, str]
    headers: dict[str, str]
    native: str


class HttpMultipartCall(TypedDict, total=False):
    transport: Literal["http_multipart"]
    method: str
    url: str
    form: dict[str, str]
    files: dict[str, Any]
    headers: dict[str, str]
    native: str


class SmtpCall(TypedDict, total=False):
    transport: Literal["smtp"]
    native: str
    email: dict[str, Any]


class TwimlCall(TypedDict, total=False):
    transport: Literal["twiml"]
    native: str
    twiml: str


class GatewayCall(TypedDict, total=False):
    transport: Literal["gateway"]
    native: str
    path: str
    json: dict[str, Any]


class NoopCall(TypedDict, total=False):
    transport: Literal["noop"]
    native: str


def http_json(
    *,
    url: str,
    native: str,
    method: str = "POST",
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Sent:
    raw: dict[str, Any] = {
        "transport": "http_json",
        "method": method,
        "url": url,
        "native": native,
    }
    if json is not None:
        raw["json"] = json
    if headers:
        raw["headers"] = headers
    return Sent(raw=raw)


def http_form(
    *,
    url: str,
    form: dict[str, str],
    native: str,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> Sent:
    raw: dict[str, Any] = {
        "transport": "http_form",
        "method": method,
        "url": url,
        "form": form,
        "native": native,
    }
    if headers:
        raw["headers"] = headers
    return Sent(raw=raw)


def http_multipart(
    *,
    url: str,
    native: str,
    method: str = "POST",
    form: dict[str, str] | None = None,
    files: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Sent:
    raw: dict[str, Any] = {
        "transport": "http_multipart",
        "method": method,
        "url": url,
        "native": native,
    }
    if form:
        raw["form"] = form
    if files:
        raw["files"] = files
    if headers:
        raw["headers"] = headers
    return Sent(raw=raw)


def smtp(*, email: dict[str, Any], native: str) -> Sent:
    return Sent(raw={"transport": "smtp", "native": native, "email": email})


def twiml(*, markup: str, native: str) -> Sent:
    return Sent(raw={"transport": "twiml", "native": native, "twiml": markup})


def gateway(*, native: str, path: str = "", json: dict[str, Any] | None = None) -> Sent:
    raw: dict[str, Any] = {"transport": "gateway", "native": native}
    if path:
        raw["path"] = path
    if json is not None:
        raw["json"] = json
    return Sent(raw=raw)


def noop(*, native: str) -> Sent:
    return Sent(raw={"transport": "noop", "native": native})
