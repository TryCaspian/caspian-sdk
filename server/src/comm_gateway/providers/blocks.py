"""Provider-neutral rich message blocks and per-channel renderers.

An agent sends a channel-agnostic list of blocks; each provider renders them to
its own native format (Slack Block Kit, Discord embeds+components, Telegram
inline keyboards, HTML email) and every other channel degrades to clean plain
text. One payload, best-possible rendering everywhere — the same block list is
rich on Slack and readable on SMS.

Wire format is plain JSON dicts (language-neutral: works from Python, TS, or raw
REST). Each block is `{"type": <kind>, ...}`. Supported kinds:

    {"type": "heading",  "text": "..."}
    {"type": "text",     "text": "..."}
    {"type": "divider"}
    {"type": "image",    "url": "...", "alt": "..."}
    {"type": "fields",   "fields": [{"label": "...", "value": "..."}]}
    {"type": "list",     "items": ["...", ...], "ordered": false}
    {"type": "buttons",  "buttons": [{"label": "...", "url": "..."}
                                     | {"label": "...", "value": "..."}]}
    {"type": "card",     "title": "...", "subtitle": "...", "image": "...",
                          "text": "...", "buttons": [...]}

A button with a `url` is a link; a button with a `value` is a callback (the tap
posts back to the agent as an `interaction` event — round-trip is a fast-follow,
but the button still renders and degrades to a "reply with …" hint on text-only
channels). Unknown block kinds and malformed fields are skipped, never raised —
a bad block must not fail a send.
"""

from __future__ import annotations

import html as _html
from collections.abc import Mapping, Sequence

# Block kinds we know how to render. Anything else is ignored.
KNOWN = {"heading", "text", "divider", "image", "fields", "list", "buttons", "card"}


def normalize(blocks) -> list[dict]:
    """Coerce arbitrary input into a clean list of known block dicts (best-effort)."""
    if not blocks:
        return []
    if isinstance(blocks, Mapping):
        blocks = [blocks]
    out: list[dict] = []
    for b in blocks:
        if isinstance(b, Mapping) and b.get("type") in KNOWN:
            out.append(dict(b))
    return out


def _buttons(block: Mapping) -> list[dict]:
    out = []
    for btn in block.get("buttons") or []:
        if not isinstance(btn, Mapping):
            continue
        label = str(btn.get("label") or btn.get("text") or "").strip()
        if not label:
            continue
        item = {"label": label}
        if btn.get("url"):
            item["url"] = str(btn["url"])
        elif btn.get("value") is not None:
            item["value"] = str(btn["value"])
        else:
            continue
        out.append(item)
    return out


def _fields(block: Mapping) -> list[tuple[str, str]]:
    out = []
    for f in block.get("fields") or []:
        if isinstance(f, Mapping):
            label = str(f.get("label", "")).strip()
            value = str(f.get("value", "")).strip()
            if label or value:
                out.append((label, value))
    return out


# --------------------------------------------------------------------------- #
# Plain text — the universal fallback (SMS, iMessage, WhatsApp, X, voice, ...).
# --------------------------------------------------------------------------- #
def to_text(blocks) -> str:
    lines: list[str] = []
    for b in normalize(blocks):
        kind = b["type"]
        if kind == "heading":
            lines.append(str(b.get("text", "")).upper())
        elif kind == "text":
            lines.append(str(b.get("text", "")))
        elif kind == "divider":
            lines.append("—" * 8)
        elif kind == "image":
            lines.append(str(b.get("url", "")))
        elif kind == "fields":
            lines.extend(f"{lbl}: {val}" if lbl else val for lbl, val in _fields(b))
        elif kind == "list":
            ordered = bool(b.get("ordered"))
            for i, item in enumerate(b.get("items") or [], 1):
                lines.append(f"{i}. {item}" if ordered else f"• {item}")
        elif kind == "buttons":
            for btn in _buttons(b):
                if "url" in btn:
                    lines.append(f"{btn['label']}: {btn['url']}")
                else:
                    lines.append(f"» reply \"{btn['label']}\"")
        elif kind == "card":
            if b.get("title"):
                lines.append(str(b["title"]).upper())
            if b.get("subtitle"):
                lines.append(str(b["subtitle"]))
            if b.get("image"):
                lines.append(str(b["image"]))
            if b.get("text"):
                lines.append(str(b["text"]))
            for btn in _buttons(b):
                lines.append(f"{btn['label']}: {btn['url']}" if "url" in btn
                             else f"» reply \"{btn['label']}\"")
        lines.append("")  # blank line between blocks
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
# HTML — email (and any html-capable channel).
# --------------------------------------------------------------------------- #
def _btn_html(btn: Mapping) -> str:
    label = _html.escape(btn["label"])
    if "url" in btn:
        href = _html.escape(btn["url"], quote=True)
        return (f'<a href="{href}" style="display:inline-block;padding:8px 14px;'
                f'margin:4px 6px 4px 0;background:#111;color:#fff;border-radius:6px;'
                f'text-decoration:none;font-size:14px">{label}</a>')
    return (f'<span style="display:inline-block;padding:8px 14px;margin:4px 6px 4px 0;'
            f'border:1px solid #ccc;border-radius:6px;font-size:14px">{label}</span>')


def to_html(blocks) -> str:
    parts: list[str] = []
    for b in normalize(blocks):
        kind = b["type"]
        if kind == "heading":
            parts.append(f"<h3>{_html.escape(str(b.get('text', '')))}</h3>")
        elif kind == "text":
            parts.append(f"<p>{_html.escape(str(b.get('text', '')))}</p>")
        elif kind == "divider":
            parts.append("<hr>")
        elif kind == "image":
            url = _html.escape(str(b.get("url", "")), quote=True)
            alt = _html.escape(str(b.get("alt", "")), quote=True)
            parts.append(f'<img src="{url}" alt="{alt}" style="max-width:100%;border-radius:8px">')
        elif kind == "fields":
            rows = "".join(
                f'<tr><td style="padding:4px 12px 4px 0"><strong>{_html.escape(lbl)}</strong></td>'
                f'<td style="padding:4px 0">{_html.escape(val)}</td></tr>'
                for lbl, val in _fields(b)
            )
            parts.append(f'<table style="border-collapse:collapse">{rows}</table>')
        elif kind == "list":
            tag = "ol" if b.get("ordered") else "ul"
            items = "".join(f"<li>{_html.escape(str(i))}</li>" for i in b.get("items") or [])
            parts.append(f"<{tag}>{items}</{tag}>")
        elif kind == "buttons":
            parts.append("<div>" + "".join(_btn_html(x) for x in _buttons(b)) + "</div>")
        elif kind == "card":
            inner = []
            if b.get("image"):
                inner.append(f'<img src="{_html.escape(str(b["image"]), quote=True)}" '
                             f'style="max-width:100%;border-radius:8px">')
            if b.get("title"):
                inner.append(f'<h3 style="margin:8px 0 2px">{_html.escape(str(b["title"]))}</h3>')
            if b.get("subtitle"):
                sub = _html.escape(str(b["subtitle"]))
                inner.append(f'<div style="color:#666;font-size:13px">{sub}</div>')
            if b.get("text"):
                inner.append(f"<p>{_html.escape(str(b['text']))}</p>")
            btns = _buttons(b)
            if btns:
                inner.append("<div>" + "".join(_btn_html(x) for x in btns) + "</div>")
            parts.append('<div style="border:1px solid #e5e5e8;border-radius:10px;'
                         'padding:14px;margin:8px 0">' + "".join(inner) + "</div>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Slack — Block Kit.
# --------------------------------------------------------------------------- #
def _slack_actions(btns: Sequence[Mapping]) -> dict | None:
    elements = []
    for i, btn in enumerate(btns[:5]):  # Slack: max 5 elements per actions block
        el = {"type": "button", "text": {"type": "plain_text", "text": btn["label"][:75]},
              "action_id": f"caspian_btn_{i}"}
        if "url" in btn:
            el["url"] = btn["url"]
        else:
            el["value"] = btn["value"][:2000]
        elements.append(el)
    return {"type": "actions", "elements": elements} if elements else None


def to_slack(blocks) -> list[dict]:
    out: list[dict] = []
    for b in normalize(blocks):
        kind = b["type"]
        if kind == "heading":
            out.append({"type": "header",
                        "text": {"type": "plain_text", "text": str(b.get("text", ""))[:150]}})
        elif kind == "text":
            out.append({"type": "section",
                        "text": {"type": "mrkdwn", "text": str(b.get("text", ""))}})
        elif kind == "divider":
            out.append({"type": "divider"})
        elif kind == "image":
            out.append({"type": "image", "image_url": str(b.get("url", "")),
                        "alt_text": str(b.get("alt", "") or "image")})
        elif kind == "fields":
            fields = [{"type": "mrkdwn", "text": f"*{lbl}*\n{val}" if lbl else val}
                      for lbl, val in _fields(b)][:10]
            if fields:
                out.append({"type": "section", "fields": fields})
        elif kind == "list":
            ordered = bool(b.get("ordered"))
            text = "\n".join(f"{i}. {item}" if ordered else f"• {item}"
                             for i, item in enumerate(b.get("items") or [], 1))
            if text:
                out.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        elif kind == "buttons":
            act = _slack_actions(_buttons(b))
            if act:
                out.append(act)
        elif kind == "card":
            if b.get("title"):
                out.append({"type": "header",
                            "text": {"type": "plain_text", "text": str(b["title"])[:150]}})
            body = "\n".join(str(b[k]) for k in ("subtitle", "text") if b.get(k))
            if body:
                out.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})
            if b.get("image"):
                out.append({"type": "image", "image_url": str(b["image"]), "alt_text": "image"})
            act = _slack_actions(_buttons(b))
            if act:
                out.append(act)
    return out


# --------------------------------------------------------------------------- #
# Discord — embeds + message components.
# --------------------------------------------------------------------------- #
def _discord_components(btns: Sequence[Mapping]) -> list[dict]:
    rows: list[dict] = []
    row: list[dict] = []
    for btn in btns:
        if "url" in btn:
            comp = {"type": 2, "style": 5, "label": btn["label"][:80], "url": btn["url"]}
        else:
            comp = {"type": 2, "style": 1, "label": btn["label"][:80],
                    "custom_id": f"caspian:{btn['value'][:90]}"}
        row.append(comp)
        if len(row) == 5:  # Discord: max 5 buttons per action row
            rows.append({"type": 1, "components": row})
            row = []
        if len(rows) == 5:  # max 5 rows
            break
    if row and len(rows) < 5:
        rows.append({"type": 1, "components": row})
    return rows


def to_discord(blocks) -> tuple[list[dict], list[dict]]:
    """Return (embeds, components). One embed aggregates text/fields/image; buttons
    become action-row components."""
    desc: list[str] = []
    fields: list[dict] = []
    image: str | None = None
    title: str | None = None
    btns: list[dict] = []
    for b in normalize(blocks):
        kind = b["type"]
        if kind == "heading":
            desc.append(f"**{b.get('text', '')}**")
        elif kind == "text":
            desc.append(str(b.get("text", "")))
        elif kind == "divider":
            desc.append("─" * 10)
        elif kind == "image":
            image = image or str(b.get("url", "")) or None
        elif kind == "fields":
            fields.extend({"name": lbl or "​", "value": val or "​", "inline": True}
                          for lbl, val in _fields(b))
        elif kind == "list":
            ordered = bool(b.get("ordered"))
            desc.extend(f"{i}. {item}" if ordered else f"• {item}"
                        for i, item in enumerate(b.get("items") or [], 1))
        elif kind == "buttons":
            btns.extend(_buttons(b))
        elif kind == "card":
            title = title or (str(b["title"]) if b.get("title") else None)
            desc.extend(str(b[k]) for k in ("subtitle", "text") if b.get(k))
            image = image or (str(b["image"]) if b.get("image") else None)
            btns.extend(_buttons(b))
    embed: dict = {}
    if title:
        embed["title"] = title[:256]
    if desc:
        embed["description"] = "\n".join(desc)[:4096]
    if fields:
        embed["fields"] = fields[:25]
    if image:
        embed["image"] = {"url": image}
    embeds = [embed] if embed else []
    return embeds, _discord_components(btns)


# --------------------------------------------------------------------------- #
# Telegram — HTML text + inline keyboard.
# --------------------------------------------------------------------------- #
def to_telegram(blocks) -> tuple[str, dict | None]:
    """Return (html_text, reply_markup). Buttons become an inline keyboard; a
    callback button uses callback_data, a link button uses url."""
    lines: list[str] = []
    keyboard: list[list[dict]] = []
    for b in normalize(blocks):
        kind = b["type"]
        if kind == "heading":
            lines.append(f"<b>{_html.escape(str(b.get('text', '')))}</b>")
        elif kind == "text":
            lines.append(_html.escape(str(b.get("text", ""))))
        elif kind == "divider":
            lines.append("—" * 8)
        elif kind == "image":
            url = str(b.get("url", ""))
            if url:
                lines.append(f'<a href="{_html.escape(url, quote=True)}">🖼</a>')
        elif kind == "fields":
            lines.extend(f"<b>{_html.escape(lbl)}:</b> {_html.escape(val)}" if lbl
                         else _html.escape(val) for lbl, val in _fields(b))
        elif kind == "list":
            ordered = bool(b.get("ordered"))
            for i, item in enumerate(b.get("items") or [], 1):
                prefix = f"{i}. " if ordered else "• "
                lines.append(prefix + _html.escape(str(item)))
        elif kind in ("buttons", "card"):
            if kind == "card":
                if b.get("title"):
                    lines.append(f"<b>{_html.escape(str(b['title']))}</b>")
                if b.get("subtitle"):
                    lines.append(_html.escape(str(b["subtitle"])))
                if b.get("image"):
                    img_url = str(b["image"])
                    lines.append(f'<a href="{_html.escape(img_url, quote=True)}">🖼</a>')
                if b.get("text"):
                    lines.append(_html.escape(str(b["text"])))
            for btn in _buttons(b):
                key = {"text": btn["label"]}
                if "url" in btn:
                    key["url"] = btn["url"]
                else:
                    key["callback_data"] = f"caspian:{btn['value']}"[:64]
                keyboard.append([key])
    markup = {"inline_keyboard": keyboard} if keyboard else None
    return "\n".join(lines).strip(), markup


def has_content(blocks) -> bool:
    return bool(normalize(blocks))
