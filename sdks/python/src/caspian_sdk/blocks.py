"""Tiny helpers for building rich message blocks.

Blocks are plain JSON-serialisable dicts, so you never need these helpers — but
they make a payload readable and guard against typos. One block list renders
natively on Slack, Discord, Telegram and email, and degrades to clean text on
every other channel.

    from caspian_sdk import blocks as b

    message.reply(blocks=[
        b.heading("Order #1024 shipped"),
        b.text("Estimated delivery: Thursday."),
        b.buttons([
            {"label": "Track package", "url": "https://example.com/track/1024"},
            {"label": "Get help", "value": "help:1024"},
        ]),
    ])

A button with a ``url`` is a link; a button with a ``value`` is a callback
(rendered as a tappable action where supported, and shown as a "reply …" hint on
text-only channels).
"""

from __future__ import annotations


def heading(text: str) -> dict:
    """Execute heading."""
    return {"type": "heading", "text": text}


def text(value: str) -> dict:
    """Execute text."""
    return {"type": "text", "text": value}


def divider() -> dict:
    """Execute divider."""
    return {"type": "divider"}


def image(url: str, alt: str | None = None) -> dict:
    """Execute image."""
    block: dict = {"type": "image", "url": url}
    if alt is not None:
        block["alt"] = alt
    return block


def fields(items: list[dict]) -> dict:
    """``items`` is a list of ``{"label": ..., "value": ...}`` dicts."""
    return {"type": "fields", "fields": items}


def bullet_list(items: list[str], ordered: bool = False) -> dict:
    """Execute bullet_list."""
    return {"type": "list", "items": items, "ordered": ordered}


def buttons(items: list[dict]) -> dict:
    """``items`` is a list of ``{"label", "url"}`` (link) or
    ``{"label", "value"}`` (callback) dicts."""
    return {"type": "buttons", "buttons": items}


def card(
    title: str | None = None,
    subtitle: str | None = None,
    image: str | None = None,
    text: str | None = None,
    buttons: list[dict] | None = None,
) -> dict:
    """Execute card."""
    block: dict = {"type": "card"}
    if title is not None:
        block["title"] = title
    if subtitle is not None:
        block["subtitle"] = subtitle
    if image is not None:
        block["image"] = image
    if text is not None:
        block["text"] = text
    if buttons is not None:
        block["buttons"] = buttons
    return block


__all__ = [
    "heading",
    "text",
    "divider",
    "image",
    "fields",
    "bullet_list",
    "buttons",
    "card",
]
