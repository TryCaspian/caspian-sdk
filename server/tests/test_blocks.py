"""Rich blocks: renderer correctness + each provider emitting its native format."""

import json

import httpx
from comm_gateway.providers import blocks
from comm_gateway.providers.base import OutboundMessage
from comm_gateway.providers.discord import DiscordProvider
from comm_gateway.providers.slack import SlackProvider
from comm_gateway.providers.telegram import TelegramProvider

SAMPLE = [
    {"type": "heading", "text": "Order #123"},
    {"type": "text", "text": "Shipped today."},
    {"type": "fields", "fields": [
        {"label": "Total", "value": "$42"}, {"label": "ETA", "value": "Tue"}]},
    {"type": "list", "items": ["Mug", "Sticker"], "ordered": False},
    {"type": "buttons", "buttons": [
        {"label": "Track", "url": "https://x.test/t"},
        {"label": "Reorder", "value": "reorder_123"},
    ]},
]


# --- renderers -------------------------------------------------------------- #

def test_to_text_flattens_everything():
    out = blocks.to_text(SAMPLE)
    assert "ORDER #123" in out
    assert "Shipped today." in out
    assert "Total: $42" in out
    assert "• Mug" in out
    assert "Track: https://x.test/t" in out       # url button -> label: url
    assert 'reply "Reorder"' in out               # callback button -> reply hint


def test_to_html_is_email_ready():
    out = blocks.to_html(SAMPLE)
    assert "<h3>Order #123</h3>" in out
    assert "<table" in out and "<strong>Total</strong>" in out
    assert '<a href="https://x.test/t"' in out
    assert "<ul>" in out


def test_to_slack_block_kit_shapes():
    kit = blocks.to_slack(SAMPLE)
    types = [b["type"] for b in kit]
    assert types[0] == "header"
    assert "section" in types
    assert "actions" in types
    actions = next(b for b in kit if b["type"] == "actions")
    els = actions["elements"]
    assert els[0]["url"] == "https://x.test/t"     # link button
    assert els[1]["value"] == "reorder_123"        # callback button carries value


def test_to_discord_embed_and_components():
    embeds, components = blocks.to_discord(SAMPLE)
    assert embeds and embeds[0]["description"]
    assert any(f["name"] == "Total" for f in embeds[0]["fields"])
    row = components[0]["components"]
    assert row[0]["style"] == 5 and row[0]["url"] == "https://x.test/t"   # link
    assert row[1]["custom_id"].startswith("caspian:")                    # callback


def test_to_telegram_html_and_keyboard():
    text, markup = blocks.to_telegram(SAMPLE)
    assert "<b>Order #123</b>" in text
    keys = [k for row in markup["inline_keyboard"] for k in row]
    assert any(k.get("url") == "https://x.test/t" for k in keys)
    assert any(k.get("callback_data", "").startswith("caspian:") for k in keys)


def test_unknown_and_malformed_blocks_are_skipped_not_raised():
    weird = [{"type": "nope"}, {"no_type": 1}, {"type": "text", "text": "kept"}, "junk"]
    assert blocks.to_text(weird) == "kept"
    assert blocks.to_slack(weird) == [
        {"type": "section", "text": {"type": "mrkdwn", "text": "kept"}}
    ]


def test_empty_blocks_are_falsey():
    assert blocks.to_text([]) == ""
    assert blocks.to_slack(None) == []
    assert not blocks.has_content([])


# --- providers emit native format on the wire ------------------------------ #

def _mock(provider, path_json):
    """Rebind provider._client to a transport that captures the last request body
    and returns a canned success for the matched path."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content) if request.content else {}
        captured["path"] = request.url.path
        for suffix, resp in path_json.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(200, json=resp)
        return httpx.Response(200, json={"ok": True})

    provider._client = httpx.Client(
        base_url=str(provider._client.base_url),
        transport=httpx.MockTransport(handler),
    )
    return captured


def test_slack_send_puts_block_kit_on_the_wire():
    p = SlackProvider(client_id="c")
    cap = _mock(p, {"/chat.postMessage": {"ok": True, "ts": "1.2"}})
    p.send("app:team", OutboundMessage(text="fallback", blocks=tuple(SAMPLE), to=("C1",)),
           credentials={"bot_token": "xoxb"})
    assert cap["body"]["text"] == "fallback"          # notification fallback kept
    assert cap["body"]["blocks"][0]["type"] == "header"


def test_discord_send_puts_embeds_and_components_on_the_wire():
    p = DiscordProvider()
    cap = _mock(p, {"/messages": {"id": "m1", "channel_id": "C1"}})
    p.send("app", OutboundMessage(text="fallback", blocks=tuple(SAMPLE), to=("C1",)),
           credentials={"bot_token": "bot"})
    assert cap["body"]["embeds"][0]["description"]
    assert cap["body"]["components"][0]["type"] == 1


def test_telegram_send_uses_html_and_inline_keyboard():
    p = TelegramProvider()
    cap = _mock(p, {"/sendMessage": {"ok": True, "result": {"message_id": 9}}})
    p.send("bot", OutboundMessage(blocks=tuple(SAMPLE), to=("999",)),
           credentials={"bot_token": "111:AAA"})
    assert cap["body"]["parse_mode"] == "HTML"
    assert cap["body"]["reply_markup"]["inline_keyboard"]


def test_no_blocks_sends_plain_text_unchanged():
    p = TelegramProvider()
    cap = _mock(p, {"/sendMessage": {"ok": True, "result": {"message_id": 9}}})
    p.send("bot", OutboundMessage(text="hi", to=("999",)), credentials={"bot_token": "111:AAA"})
    assert cap["body"]["text"] == "hi"
    assert "parse_mode" not in cap["body"]
