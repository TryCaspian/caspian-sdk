"""Commands: inbound commands parse into command.received events,
and individual providers verify signatures / normalize command payloads offline.
"""

import pytest
from comm_gateway.config import Settings
from comm_gateway.jobs import run_pending_jobs
from comm_gateway.listeners.discord_gateway import DiscordGatewayClient
from comm_gateway.main import create_app
from comm_gateway.providers.discord import parse_gateway_command
from comm_gateway.providers.fakes.fake_social import FakeSlackProvider
from comm_gateway.providers.slack import SlackProvider, parse_slack_command
from comm_gateway.providers.telegram import parse_update
from fastapi.testclient import TestClient

API_KEY = "comm_test_key"


# --- Slack Slash Commands ---------------------------------------------------- #

def test_slack_command_event_parses():
    payload = {
        "command": "/caspian",
        "text": "help settings",
        "user_id": "U999",
        "user_name": "alice",
        "channel_id": "C888",
        "trigger_id": "trig123",
        "api_app_id": "A111",
        "team_id": "T222",
    }
    msgs = parse_slack_command(payload)
    assert len(msgs) == 1
    cmd = msgs[0]
    assert cmd.kind == "command"
    assert cmd.command == {
        "command": "caspian",
        "text": "help settings",
        "source_message_id": None,
    }
    assert cmd.sender_address == "U999"
    assert cmd.sender_name == "alice"
    assert cmd.provider_thread_id == "C888"


def test_slack_webhook_content_type_branching():
    import time

    from comm_gateway.providers.base import WebhookVerificationError

    p = SlackProvider(client_id="c", signing_secret="fake-signing")
    # Verify signature verification failure raises WebhookVerificationError on form body
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": "v0=wrong",
    }
    body = b"command=%2Ftest&api_app_id=A1&team_id=T1"
    with pytest.raises(WebhookVerificationError):
        p.parse_webhook(body, headers)


# --- Telegram Bot Commands --------------------------------------------------- #

def test_telegram_dm_command_parses():
    # Authoritative entity
    data = {
        "update_id": 10001,
        "message": {
            "message_id": 1,
            "from": {"id": 123, "username": "alice", "first_name": "Alice"},
            "chat": {"id": 456, "type": "private"},
            "date": 1700000000,
            "text": "/start foo bar",
            "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
        }
    }
    msgs = parse_update(data, "bot123")
    assert len(msgs) == 1
    assert msgs[0].kind == "command"
    assert msgs[0].command == {
        "command": "start",
        "text": "foo bar",
        "source_message_id": "456:1",
    }


def test_telegram_group_command_with_suffix_parses():
    # Suffix "@MyBot" in supergroup
    data = {
        "update_id": 10002,
        "message": {
            "message_id": 2,
            "from": {"id": 123, "username": "alice", "first_name": "Alice"},
            "chat": {"id": -999, "type": "supergroup"},
            "date": 1700000000,
            "text": "/help@MyBot settings reset",
            "entities": [{"type": "bot_command", "offset": 0, "length": 11}],
        }
    }
    msgs = parse_update(data, "bot123")
    assert len(msgs) == 1
    assert msgs[0].kind == "command"
    assert msgs[0].command == {
        "command": "help",
        "text": "settings reset",
        "source_message_id": "-999:2",
    }


def test_telegram_command_fallback_regex():
    # Fallback to regex when entities are missing
    data = {
        "update_id": 10003,
        "message": {
            "message_id": 3,
            "from": {"id": 123, "username": "alice"},
            "chat": {"id": 456, "type": "private"},
            "date": 1700000000,
            "text": "/info@MyBot args here",
        }
    }
    msgs = parse_update(data, "bot123")
    assert len(msgs) == 1
    assert msgs[0].kind == "command"
    assert msgs[0].command["command"] == "info"
    assert msgs[0].command["text"] == "args here"


# --- Discord Slash Commands & Ack -------------------------------------------- #

def test_discord_command_nested_subcommand_parses():
    # Application Command type 2
    event = {
        "type": 2,
        "id": "int123",
        "guild_id": "G1",
        "channel_id": "C1",
        "data": {
            "name": "settings",
            "options": [
                {
                    "name": "channel",
                    "type": 2,  # SUB_COMMAND_GROUP
                    "options": [
                        {
                            "name": "set",
                            "type": 1,  # SUB_COMMAND
                            "options": [
                                {
                                    "name": "value",
                                    "type": 3,
                                    "value": "general"
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        "member": {
            "user": {"id": "U1", "username": "bob"}
        }
    }
    msgs = parse_gateway_command(event, "app123")
    assert len(msgs) == 1
    cmd = msgs[0]
    assert cmd.kind == "command"
    assert cmd.command["command"] == "settings channel set"
    assert cmd.command["text"] == "general"


def test_discord_interaction_create_is_not_command():
    # Component type 3 should yield empty for command parser
    event = {
        "type": 3,
        "id": "int123",
        "data": {
            "custom_id": "caspian:click",
            "component_type": 2
        }
    }
    msgs = parse_gateway_command(event, "app123")
    assert msgs == []


def test_discord_ack_behavior_regression():
    # Verify that MESSAGE_COMPONENT interaction yields callback type 6 (DEFERRED_UPDATE_MESSAGE)
    # while APPLICATION_COMMAND yields callback type 5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)
    client = DiscordGatewayClient(
        bot_token="tok",
        app_id="app123",
        on_message=lambda msg: None,
        api_base="http://localhost",
    )

    # To bypass raw websockets connection, we call _ack_interaction directly
    import asyncio
    import unittest.mock as mock

    with mock.patch("httpx.AsyncClient") as mock_client:
        mock_post = mock.AsyncMock()
        mock_client.return_value.__aenter__.return_value.post = mock_post

        # Test command type 2 ack
        asyncio.run(client._ack_interaction({"id": "1", "token": "a", "type": 2}))
        mock_post.assert_called_with("http://localhost/interactions/1/a/callback", json={"type": 5})

        mock_post.reset_mock()

        # Test component type 3 ack
        asyncio.run(client._ack_interaction({"id": "2", "token": "b", "type": 3}))
        mock_post.assert_called_with("http://localhost/interactions/2/b/callback", json={"type": 6})


# --- End-to-End Gateway Event Emission --------------------------------------- #

def _slack_active(app, provider):
    sc = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})
    conn = sc.post("/v1/connections/slack/install", json={}).json()
    with app.state.session_factory() as session:
        from comm_gateway.models import Connection
        state = session.get(Connection, conn["id"]).provider_credentials["oauth_state"]
    TestClient(app).get(f"/v1/oauth/{provider.name}/callback",
                        params={"code": "abc", "state": state}, follow_redirects=True)
    run_pending_jobs(app.state.session_factory, app.state.providers)
    return sc, conn


def test_slack_inbound_command_emits_event_end_to_end():
    settings = Settings(database_url="sqlite://", bootstrap_api_key=API_KEY,
                        inline_worker=False, public_base_url="https://gw.test")
    provider = FakeSlackProvider()
    app = create_app(settings, providers={provider.name: provider})
    sc, conn = _slack_active(app, provider)
    anon = TestClient(app)

    # Trigger command payload
    payload = provider.command_payload(command="/settings", text="theme dark")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    anon.post(f"/internal/providers/{provider.name}/webhooks", content=payload, headers=headers)
    run_pending_jobs(app.state.session_factory, app.state.providers)

    events = sc.get("/v1/events", params={"type": "command.received"}).json()
    assert len(events) == 1
    event_data = events[0]["data"]
    assert event_data["command"] == "settings"
    assert event_data["text"] == "theme dark"
    assert event_data["connection_id"] == conn["id"]
    assert event_data["conversation_id"] is not None


def test_find_or_create_conversation_concurrency_retry(app):
    from comm_gateway.jobs import _find_or_create_conversation
    from comm_gateway.models import Connection, Conversation

    session_factory = app.state.session_factory
    with session_factory() as session:
        # 1. Setup a Connection and an existing Conversation
        conn = Connection(
            id="conn_concurrency_test",
            project_id="proj_1",
            customer_id="cus_1",
            agent_id="agt_1",
            provider="fake-slack",
            provider_resource_id="res_1",
            status="active",
            provider_credentials={},
        )
        session.add(conn)
        session.commit()

        conv = Conversation(
            id="conv_existing",
            project_id="proj_1",
            connection_id=conn.id,
            provider_thread_id="thread_1",
        )
        session.add(conv)
        session.commit()

        # 2. Intercept query lookup to return None on first check, simulating a race
        orig_execute = session.execute
        call_count = 0

        def mock_execute(statement, *args, **kwargs):
            nonlocal call_count
            is_conversation_select = False
            try:
                sql_str = str(statement)
                if "SELECT" in sql_str and "conversations" in sql_str:
                    is_conversation_select = True
            except Exception:
                pass

            if is_conversation_select:
                call_count += 1
                if call_count == 1:
                    class MockResult:
                        def scalar_one_or_none(self):
                            return None
                    return MockResult()

            return orig_execute(statement, *args, **kwargs)

        session.execute = mock_execute

        # 3. Call _find_or_create_conversation. The lookup will return None,
        # forcing it to insert and trigger unique constraint (IntegrityError).
        # We assert it gracefully recovers and returns the existing conversation ID.
        res = _find_or_create_conversation(session, conn, "thread_1", "subject")
        assert res is not None
        assert res.id == "conv_existing"

