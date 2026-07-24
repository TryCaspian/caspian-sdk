"""Telegram bot adapter: update normalization and webhook scoping/secret."""

import json

import httpx
import pytest
from caspian_adapters.base import Attachment, OutboundMessage, WebhookVerificationError
from caspian_adapters.telegram import TelegramProvider, bot_id_from_token, parse_update

BOT_TOKEN = "7123456789:AAEexample"
BOT_ID = "7123456789"


def _update(
    text: str | None = "hi there",
    update_id: int = 100,
    edited: bool = False,
    chat_type: str = "private",
):
    message = {
        "message_id": 55,
        "chat": {"id": 900, "type": chat_type},
        "from": {
            "id": 42,
            "username": "alice",
            "first_name": "Alice",
            "last_name": "Ng",
        },
    }

    if text is not None:
        message["text"] = text

    return {
        "update_id": update_id,
        ("edited_message" if edited else "message"): message,
    }


def test_bot_id_from_token():
    assert bot_id_from_token(BOT_TOKEN) == BOT_ID


def test_parse_update_normalizes_message():
    inbound = parse_update(_update(), BOT_ID)
    assert len(inbound) == 1
    assert inbound[0].text == "hi there"
    assert inbound[0].provider_inbox_id == BOT_ID
    assert inbound[0].provider_message_id == "900:55"
    assert inbound[0].sender_address == "alice"
    assert inbound[0].sender_name == "Alice Ng"
    assert inbound[0].edited is False


def test_parse_update_marks_edited_and_group_chats():
    inbound = parse_update(_update(edited=True, chat_type="group"), BOT_ID)
    assert inbound[0].edited is True
    assert inbound[0].chat_type == "group"


def test_parse_update_skips_messages_without_text_or_attachments():
    assert parse_update({"update_id": 1, "message": {"chat": {"id": 1}}}, BOT_ID) == []
    assert parse_update({"update_id": 2}, BOT_ID) == []


def test_parse_webhook_requires_connection_scope():
    provider = TelegramProvider()
    with pytest.raises(WebhookVerificationError, match="connection scope"):
        provider.parse_webhook(b"{}", {}, credentials=None)


def test_parse_webhook_enforces_secret_header():
    provider = TelegramProvider()
    payload = json.dumps(_update()).encode()
    creds = {"bot_token": BOT_TOKEN, "webhook_secret": "shh"}
    inbound = provider.parse_webhook(
        payload, {"X-Telegram-Bot-Api-Secret-Token": "shh"}, credentials=creds
    )
    assert inbound[0].text == "hi there"
    with pytest.raises(WebhookVerificationError, match="secret token"):
        provider.parse_webhook(
            payload, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}, credentials=creds
        )
    # A missing header must reject cleanly, not raise on the constant-time compare.
    with pytest.raises(WebhookVerificationError, match="secret token"):
        provider.parse_webhook(payload, {}, credentials=creds)


def test_parse_webhook_without_secret_skips_check():
    provider = TelegramProvider()
    payload = json.dumps(_update()).encode()
    inbound = provider.parse_webhook(payload, {}, credentials={"bot_token": BOT_TOKEN})
    assert inbound[0].provider_inbox_id == BOT_ID

def test_parse_update_parses_photo():
    update = _update(text=None)
    update["message"]["photo"] = [
        {
            "file_id": "photo123",
            "file_size": 1024,
        }
    ]

    inbound = parse_update(update, BOT_ID)

    assert len(inbound) == 1
    assert inbound[0].text is None
    assert len(inbound[0].attachments) == 1

    attachment = inbound[0].attachments[0]
    assert attachment.provider_file_id == "photo123"
    assert attachment.mime_type == "image/jpeg"
    assert attachment.size_bytes == 1024

def test_parse_update_parses_document():
    update = _update(text=None)
    update["message"]["document"] = {
        "file_id": "doc123",
        "file_name": "report.pdf",
        "mime_type": "application/pdf",
        "file_size": 2048,
    }

    inbound = parse_update(update, BOT_ID)

    assert len(inbound) == 1
    assert len(inbound[0].attachments) == 1

    attachment = inbound[0].attachments[0]
    assert attachment.provider_file_id == "doc123"
    assert attachment.filename == "report.pdf"
    assert attachment.mime_type == "application/pdf"
    assert attachment.size_bytes == 2048

def test_parse_update_parses_voice():
    update = _update(text=None)
    update["message"]["voice"] = {
        "file_id": "voice123",
        "mime_type": "audio/ogg",
        "file_size": 4096,
    }

    inbound = parse_update(update, BOT_ID)

    assert len(inbound) == 1
    assert len(inbound[0].attachments) == 1

    attachment = inbound[0].attachments[0]
    assert attachment.provider_file_id == "voice123"
    assert attachment.mime_type == "audio/ogg"
    assert attachment.size_bytes == 4096

def test_parse_update_uses_caption_as_text():
    update = _update(text=None)
    update["message"]["caption"] = "A photo"
    update["message"]["photo"] = [
        {
            "file_id": "photo123",
            "file_size": 1024,
        }
    ]

    inbound = parse_update(update, BOT_ID)

    assert len(inbound) == 1
    assert inbound[0].text == "A photo"
    assert len(inbound[0].attachments) == 1


def test_send_processes_every_attachment():
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(seen)}})

    provider = TelegramProvider()
    provider._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
        timeout=30.0,
    )

    result = provider.send(
        "inbox",
        OutboundMessage(
            text="caption",
            to=("777",),
            attachments=(
                Attachment(provider_file_id="photo123", mime_type="image/png"),
                Attachment(url="https://example.com/report.pdf", mime_type="application/pdf"),
            ),
        ),
        credentials={"bot_token": BOT_TOKEN},
    )

    assert [path for path, _ in seen] == [
        f"/bot{BOT_TOKEN}/sendPhoto",
        f"/bot{BOT_TOKEN}/sendDocument",
    ]
    assert seen[0][1]["photo"] == "photo123"
    assert seen[0][1]["caption"] == "caption"
    assert seen[1][1]["document"] == "https://example.com/report.pdf"
    assert seen[1][1]["caption"] == ""
    assert result.provider_thread_id == "777"
    assert result.provider_message_id == "777:2"


def test_reply_uses_url_fallback_and_keeps_reply_fields():
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})

    provider = TelegramProvider()
    provider._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
        timeout=30.0,
    )

    result = provider.reply(
        "inbox",
        "777:55",
        OutboundMessage(
            text="answer",
            attachments=(Attachment(url="https://example.com/voice.ogg", mime_type="audio/ogg"),),
        ),
        credentials={"bot_token": BOT_TOKEN},
    )

    assert seen[0][0] == f"/bot{BOT_TOKEN}/sendVoice"
    assert seen[0][1]["voice"] == "https://example.com/voice.ogg"
    assert seen[0][1]["reply_to_message_id"] == 55
    assert seen[0][1]["allow_sending_without_reply"] is True
    assert result.provider_thread_id == "777"
    assert result.provider_message_id == "777:11"
