"""Interpret Intent. Hosted I/O goes through an injected Gateway."""

from __future__ import annotations

from typing import Any

from caspian_cli.catalog import get_catalog, load_catalog, search_catalog
from caspian_cli.gateway import Gateway
from caspian_cli.intent import (
    Call,
    CatalogGet,
    CatalogList,
    CatalogSearch,
    ChannelsAdd,
    ChannelsLs,
    Intent,
    ThreadsLs,
    ThreadsTail,
)

HOSTED_INSTALL = frozenset({"slack", "discord", "x", "github"})


def conversation_of(thread_id: str) -> str:
    at = thread_id.find(":")
    return thread_id if at < 0 else thread_id[at + 1 :]


def run_intent(intent: Intent, *, gateway: Gateway) -> Any:
    if isinstance(intent, ChannelsAdd):
        return _channels_add(intent, gateway)
    if isinstance(intent, ChannelsLs):
        return gateway.request("GET", "/v1/connections", None)
    if isinstance(intent, CatalogList):
        return load_catalog()
    if isinstance(intent, CatalogSearch):
        return search_catalog(intent.query)
    if isinstance(intent, CatalogGet):
        return get_catalog(intent.id)
    if isinstance(intent, Call):
        return _call(intent, gateway)
    if isinstance(intent, ThreadsLs):
        rows = gateway.request("GET", "/v1/conversations", None) or []
        if not intent.channel:
            return rows
        return [
            row
            for row in rows
            if str(row.get("channel", "")) == intent.channel
            or str(row.get("id", "")).startswith(f"{intent.channel}:")
        ]
    if isinstance(intent, ThreadsTail):
        return gateway.request("GET", "/v1/events", None)
    raise SystemExit(f"unhandled intent: {type(intent).__name__}")


def _channels_add(intent: ChannelsAdd, gateway: Gateway) -> Any:
    if intent.via == "self-host":
        if not intent.bot_token:
            raise SystemExit(
                f"Self-host {intent.channel!r} requires --bot-token. "
                "Omit --via for hosted (Caspian owns the identity)."
            )
        return {
            "channel": intent.channel,
            "via": "self-host",
            "webhook_url": intent.webhook_url,
            "inbound": intent.inbound,
        }
    path = (
        f"/v1/connections/{intent.channel}/install"
        if intent.channel in HOSTED_INSTALL
        else f"/v1/connections/{intent.channel}"
    )
    body: dict[str, Any] = {"wait": True}
    if intent.display_name:
        body["display_name"] = intent.display_name
    return gateway.request("POST", path, body)


def _call(intent: Call, gateway: Gateway) -> Any:
    try:
        entry = get_catalog(intent.id)
    except KeyError:
        raise SystemExit(f"unknown id {intent.id!r}; caspian catalog search …") from None
    tag = entry["command_tag"]
    if tag == "Post":
        thread_id = str(intent.args.get("thread_id", ""))
        cid = conversation_of(thread_id)
        text = str(intent.args.get("text", ""))
        return gateway.request(
            "POST",
            f"/v1/conversations/{cid}/messages",
            {"text": text},
        )
    if tag == "SendMedia":
        raise SystemExit(
            "SendMedia is not available in hosted mode: the gateway exposes no "
            "endpoint for it. Use self-host for this."
        )
    raise SystemExit(f"{tag} is not available in hosted mode")
