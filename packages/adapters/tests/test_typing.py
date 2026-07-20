"""Typing / 'thinking…' indicator: provider methods + the gateway route.

The SDK fires this the moment an inbound message is dispatched, so the human sees
the agent working while the handler runs. Discord + Telegram have native typing
signals; other channels are a silent no-op.
"""

import httpx
from caspian_adapters.discord import DiscordProvider
from caspian_adapters.telegram import TelegramProvider


def test_discord_typing_hits_channel_typing_endpoint():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(204)

    p = DiscordProvider(base_url="https://discord.test", shared_bot_token="")
    p._client = httpx.Client(base_url="https://discord.test",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    p.typing("chan123", credentials={"bot_token": "BOT"})
    assert seen["path"] == "/channels/chan123/typing"
    assert seen["auth"] == "Bot BOT"


def test_telegram_typing_sends_chat_action():
    seen = {}

    def handler(request):
        import json
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    p = TelegramProvider(webhook_base="", base_url="https://tg.test")
    p._client = httpx.Client(base_url="https://tg.test",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    p.typing("55555", credentials={"bot_token": "123:ABC"})
    assert seen["path"] == "/bot123:ABC/sendChatAction"
    assert seen["body"] == {"chat_id": "55555", "action": "typing"}
