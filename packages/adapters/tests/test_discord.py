"""Discord adapter: gateway-event normalization, routing modes, webhook URLs."""

import pytest
from caspian_adapters.base import InboundCommand, InboundReaction
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


# --- Reactions ---


def _reaction_event(emoji="👍", action="MESSAGE_REACTION_ADD", channel_id="chan42",
                    message_id="700001", user_id="555", guild_id="G1"):
    data = {
        "channel_id": channel_id,
        "message_id": message_id,
        "emoji": {"name": emoji},
        "user_id": user_id,
    }
    if guild_id is not None:
        data["guild_id"] = guild_id
    return {"t": action, "d": data}


def test_parse_reaction_add():
    inbound = parse_gateway_message(_reaction_event(action="MESSAGE_REACTION_ADD"), APP_ID)
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].emoji == "👍"
    assert inbound[0].action == "added"
    assert inbound[0].source_provider_message_id == "chan42:700001"
    assert inbound[0].sender_address == "555"


def test_parse_reaction_remove():
    inbound = parse_gateway_message(_reaction_event(action="MESSAGE_REACTION_REMOVE"), APP_ID)
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundReaction)
    assert inbound[0].action == "removed"


def test_parse_reaction_shared_bot_routes_by_guild():
    inbound = parse_gateway_message(
        _reaction_event(guild_id="G77"), APP_ID, route_by_guild=True
    )
    assert inbound[0].provider_inbox_id == "G77"


def test_parse_reaction_shared_bot_drops_dms():
    assert parse_gateway_message(
        _reaction_event(guild_id=None), APP_ID, route_by_guild=True
    ) == []


def test_parse_reaction_empty_emoji():
    event = _reaction_event()
    event["d"]["emoji"]["name"] = ""
    assert parse_gateway_message(event, APP_ID) == []


# --- Slash commands ---


def _interaction_event(command="deploy", options=None, channel_id="chan42",
                       user_id="555", guild_id="G1", interaction_id="int1"):
    data = {
        "id": interaction_id,
        "type": 2,
        "channel_id": channel_id,
        "data": {
            "name": command,
            "options": [{"name": k, "value": v} for k, v in (options or {}).items()],
        },
        "member": {"user": {"id": user_id, "username": "testuser"}},
    }
    if guild_id is not None:
        data["guild_id"] = guild_id
    return {"t": "INTERACTION_CREATE", "d": data}


def test_parse_slash_command():
    inbound = parse_gateway_message(
        _interaction_event(command="deploy", options={"env": "staging"}), APP_ID
    )
    assert len(inbound) == 1
    assert isinstance(inbound[0], InboundCommand)
    assert inbound[0].command == "deploy"
    assert inbound[0].args == "staging"
    assert inbound[0].text == "/deploy staging"
    assert inbound[0].sender_address == "testuser"


def test_parse_slash_command_no_options():
    inbound = parse_gateway_message(_interaction_event(command="ping"), APP_ID)
    assert inbound[0].command == "ping"
    assert inbound[0].args is None
    assert inbound[0].text == "/ping"


def test_parse_slash_command_shared_bot():
    inbound = parse_gateway_message(
        _interaction_event(guild_id="G77"), APP_ID, route_by_guild=True
    )
    assert inbound[0].provider_inbox_id == "G77"


def test_parse_slash_command_dm():
    inbound = parse_gateway_message(_interaction_event(guild_id=None), APP_ID)
    assert inbound[0].chat_type == "dm"


def test_parse_non_command_interaction_ignored():
    event = _interaction_event()
    event["d"]["type"] = 3  # message component (button), not slash command
    assert parse_gateway_message(event, APP_ID) == []
