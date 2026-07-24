"""Typing / 'thinking…' indicator: provider methods + the gateway route.

The SDK fires this the moment an inbound message is dispatched, so the human sees
the agent working while the handler runs. Discord + Telegram have native typing
signals; other channels are a silent no-op.
"""

import httpx
import pytest
from caspian_adapters.discord import DiscordProvider
from caspian_adapters.slack import SlackProvider
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


def test_telegram_edit_message():
    seen = {}

    def handler(request):
        import json
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    p = TelegramProvider(webhook_base="", base_url="https://tg.test")
    p._client = httpx.Client(base_url="https://tg.test",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    p.edit_message("55555:42", "updated text", credentials={"bot_token": "123:ABC"})
    assert seen["path"] == "/bot123:ABC/editMessageText"
    assert seen["body"] == {"chat_id": "55555", "message_id": 42, "text": "updated text"}


def test_discord_edit_message():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "99", "content": "updated"})

    p = DiscordProvider(base_url="https://discord.test", shared_bot_token="")
    p._client = httpx.Client(base_url="https://discord.test",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    p.edit_message("chan123:msg456", "updated text", credentials={"bot_token": "BOT"})
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/channels/chan123/messages/msg456"
    assert seen["auth"] == "Bot BOT"


def test_discord_edit_message_webhook_only(monkeypatch):
    seen = {}

    def mock_patch(url, **kwargs):
        seen["url"] = str(url)
        seen["body"] = kwargs.get("json")
        return httpx.Response(200, json={"id": "99", "content": "updated"},
                              request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx, "patch", mock_patch)
    p = DiscordProvider(base_url="https://discord.test", shared_bot_token="")
    creds = {"webhook_url": "https://discord.com/api/webhooks/111/tok"}
    p.edit_message("chan123:msg456", "updated text", credentials=creds)
    assert "/webhooks/111/tok/messages/msg456" in seen["url"]
    assert seen["body"] == {"content": "updated text"}


def test_slack_edit_message_application_failure():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "cant_update_message"})

    p = SlackProvider(base_url="https://slack.test")
    p._client = httpx.Client(base_url="https://slack.test",
                             transport=httpx.MockTransport(handler), timeout=5.0)
    with pytest.raises(RuntimeError, match="cant_update_message"):
        p.edit_message("C123:1234567890.123456", "new text",
                       credentials={"bot_token": "xoxb-fake"})
