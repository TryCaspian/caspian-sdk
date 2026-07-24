"""Discord adapter: gateway-event normalization, routing modes, webhook URLs."""

import pytest
from caspian_adapters.discord import install_url, parse_gateway_message, webhook_id_from_url

APP_ID = "999"


def _event(text="hey bot", guild_id="G1", author_bot=False, **data_extra):
    data = {
        "id": "700001",
        "channel_id": "chan42",
        "content": text,
        "author": {"id": "555", "username": "customer", "bot": author_bot},
        **data_extra,
    }
    if guild_id is not None:
        data["guild_id"] = guild_id
    return {"t": "MESSAGE_CREATE", "d": data}


def test_parse_normalizes_message():
    inbound = parse_gateway_message(_event(), APP_ID)
    assert len(inbound) == 1
    assert inbound[0].text == "hey bot"
    assert inbound[0].provider_inbox_id == APP_ID  # BYO bot routes by application
    assert inbound[0].provider_thread_id == "chan42"
    assert inbound[0].chat_type == "guild"


def test_parse_skips_bots_other_events_and_empty():
    assert parse_gateway_message(_event(author_bot=True), APP_ID) == []
    assert parse_gateway_message({"t": "TYPING_START", "d": {}}, APP_ID) == []
    assert parse_gateway_message(_event(text=""), APP_ID) == []


def test_shared_bot_routes_by_guild_and_drops_dms():
    inbound = parse_gateway_message(_event(guild_id="G77"), APP_ID, route_by_guild=True)
    assert inbound[0].provider_inbox_id == "G77"
    assert parse_gateway_message(_event(guild_id=None), APP_ID, route_by_guild=True) == []


def test_dm_chat_type_without_guild():
    inbound = parse_gateway_message(_event(guild_id=None), APP_ID)
    assert inbound[0].chat_type == "dm"


def test_parse_extracts_attachments():
    event = _event(
        text="see file",
        attachments=[
            {"id": "a1", "filename": "photo.png", "size": 2048,
             "url": "https://cdn.discord/photo.png", "content_type": "image/png"},
        ],
    )
    [inbound] = parse_gateway_message(event, APP_ID)
    assert inbound.text == "see file"
    assert len(inbound.attachments) == 1
    att = inbound.attachments[0]
    assert att.url == "https://cdn.discord/photo.png"
    assert att.mime_type == "image/png"
    assert att.filename == "photo.png"
    assert att.size_bytes == 2048


def test_parse_keeps_attachment_only_message():
    # No text, but an attachment is present — must not be dropped.
    event = _event(
        text="",
        attachments=[
            {"id": "a1", "filename": "doc.pdf", "size": 10,
             "url": "https://cdn/doc.pdf", "content_type": "application/pdf"},
        ],
    )
    [inbound] = parse_gateway_message(event, APP_ID)
    assert inbound.text is None
    assert inbound.attachments[0].filename == "doc.pdf"


def test_webhook_id_from_url():
    url = "https://discord.com/api/webhooks/123456/tok-en"
    assert webhook_id_from_url(url) == "123456"
    with pytest.raises(ValueError):
        webhook_id_from_url("https://discord.com/api/nope")


def test_install_url_shape():
    url = install_url(
        "https://discord.com/api/v10", "client-1", "67177472",
        "https://gw.example.com/cb", "state-1",
    )
    assert url.startswith("https://discord.com/oauth2/authorize?")
    assert "client_id=client-1" in url
    assert "permissions=67177472" in url
    assert "state=state-1" in url
