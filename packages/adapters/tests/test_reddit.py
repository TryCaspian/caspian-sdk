"""Reddit adapter parser tests."""

import httpx
import pytest
from caspian_adapters.base import InboundMessage, OutboundMessage
from caspian_adapters.reddit import RedditProvider, parse_inbox_response


def test_normal_private_message():
    payload = {
        "data": {
            "children": [
                {
                    "kind": "t4",
                    "data": {
                        "name": "t4_123abc",
                        "first_message_name": "t4_123abc",
                        "author": "reddit_user",
                        "subject": "Hello",
                        "body": "Hi there",
                    }
                }
            ]
        }
    }

    inbound = parse_inbox_response(payload, provider_inbox_id="my_bot")

    assert len(inbound) == 1
    msg = inbound[0]
    assert isinstance(msg, InboundMessage)
    assert msg.external_event_id == "t4_123abc"
    assert msg.provider_inbox_id == "my_bot"
    assert msg.provider_message_id == "t4_123abc"
    assert msg.provider_thread_id == "t4_123abc"
    assert msg.sender_address == "reddit_user"
    assert msg.sender_name == "reddit_user"
    assert msg.subject == "Hello"
    assert msg.text == "Hi there"
    assert msg.chat_type == "private"
    assert msg.edited is False
    assert msg.attachments == []
    assert msg.recipients == []
    assert msg.auto_generated is False


def test_reply_message_uses_first_message_name():
    payload = {
        "data": {
            "children": [
                {
                    "kind": "t4",
                    "data": {
                        "name": "t4_456def",
                        "first_message_name": "t4_123abc",
                        "author": "reply_user",
                        "subject": "Re: Hello",
                        "body": "This is a reply",
                    }
                }
            ]
        }
    }

    inbound = parse_inbox_response(payload, provider_inbox_id="my_bot")

    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.provider_message_id == "t4_456def"
    assert msg.provider_thread_id == "t4_123abc"


def test_mixed_inbox_skips_non_t4_objects():
    payload = {
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "name": "t1_abc123",
                        "author": "comment_user",
                        "body": "A comment reply",
                    }
                },
                {
                    "kind": "t4",
                    "data": {
                        "name": "t4_789ghi",
                        "first_message_name": "t4_789ghi",
                        "author": "message_user",
                        "subject": "Another msg",
                        "body": "A private msg",
                    }
                }
            ]
        }
    }

    inbound = parse_inbox_response(payload, provider_inbox_id="my_bot")

    assert len(inbound) == 1
    assert inbound[0].external_event_id == "t4_789ghi"


def test_missing_author_sets_auto_generated():
    payload = {
        "data": {
            "children": [
                {
                    "kind": "t4",
                    "data": {
                        "name": "t4_auto1",
                        "first_message_name": "t4_auto1",
                        "subject": "Welcome",
                        "body": "Welcome to Reddit",
                    }
                }
            ]
        }
    }

    inbound = parse_inbox_response(payload, provider_inbox_id="my_bot")

    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.auto_generated is True
    assert msg.sender_address is None
    assert msg.sender_name is None


def test_malformed_t4_payload_is_ignored():
    payload = {
        "data": {
            "children": [
                {
                    "kind": "t4",
                    "data": "not a dictionary"
                },
                {
                    "kind": "t4",
                    "data": {
                        "first_message_name": "t4_missing",
                        "author": "user",
                        "body": "body"
                    }
                }
            ]
        }
    }

    inbound = parse_inbox_response(payload, provider_inbox_id="my_bot")
    assert inbound == []


def test_empty_inbox_returns_empty_list():
    inbound = parse_inbox_response({"data": {"children": []}}, provider_inbox_id="my_bot")
    assert inbound == []

    inbound2 = parse_inbox_response({}, provider_inbox_id="my_bot")
    assert inbound2 == []

    inbound3 = parse_inbox_response({"data": "invalid"}, provider_inbox_id="my_bot")
    assert inbound3 == []

def test_reddit_provider_send(monkeypatch):
    provider = RedditProvider()

    def mock_request(method, url, **kwargs):
        assert method == "POST"
        assert url == "https://oauth.reddit.com/api/compose"
        assert kwargs["data"]["to"] == "recipient_user"
        assert kwargs["data"]["subject"] == "Test Sub"
        assert kwargs["data"]["text"] == "Hello"
        assert kwargs["headers"]["Authorization"] == "bearer valid_token"

        response = httpx.Response(
            200, json={"json": {"errors": [], "data": {}}}, request=httpx.Request("POST", url)
        )
        return response

    monkeypatch.setattr(provider._client, "request", mock_request)

    msg = OutboundMessage(to=("recipient_user",), subject="Test Sub", text="Hello")
    result = provider.send("inbox_1", msg, {"bot_token": "valid_token"})

    assert result.provider_thread_id == "recipient_user"
    assert result.provider_message_id == ""

def test_reddit_provider_reply(monkeypatch):
    provider = RedditProvider()

    def mock_request(method, url, **kwargs):
        assert method == "POST"
        assert url == "https://oauth.reddit.com/api/comment"
        assert kwargs["data"]["thing_id"] == "t4_parent123"
        assert kwargs["data"]["text"] == "Reply text"

        return httpx.Response(
            200,
            json={"json": {"errors": [], "data": {"things": [{"data": {"name": "t1_newreply"}}]}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(provider._client, "request", mock_request)

    msg = OutboundMessage(to=("recipient_user",), text="Reply text")
    result = provider.reply("inbox_1", "t4_parent123", msg, {"bot_token": "valid_token"})

    assert result.provider_message_id == "t1_newreply"
    assert result.provider_thread_id == "t4_parent123"

def test_reddit_provider_application_error(monkeypatch):
    provider = RedditProvider()

    def mock_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={"json": {"errors": [["RATELIMIT", "You are doing that too much", "text"]]}},
            request=httpx.Request("POST", ""),
        )

    monkeypatch.setattr(provider._client, "request", mock_request)

    msg = OutboundMessage(to=("recipient_user",), text="Reply text")
    with pytest.raises(RuntimeError) as exc:
        provider.reply("inbox_1", "t4_parent123", msg, {"bot_token": "valid_token"})

    assert "Reddit API error" in str(exc.value)

def test_reddit_provider_http_error(monkeypatch):
    provider = RedditProvider()

    def mock_request(*args, **kwargs):
        request = httpx.Request("POST", "https://oauth.reddit.com/api/comment")
        return httpx.Response(403, request=request)

    monkeypatch.setattr(provider._client, "request", mock_request)

    msg = OutboundMessage(to=("recipient_user",), text="Reply text")
    with pytest.raises(httpx.HTTPStatusError):
        provider.reply("inbox_1", "t4_parent123", msg, {"bot_token": "valid_token"})


def test_reddit_provider_needs_refresh():
    import time
    provider = RedditProvider()
    
    assert provider.needs_refresh(None) is False
    assert provider.needs_refresh({}) is False
    assert provider.needs_refresh({"refresh_token": "abc"}) is False
    
    # 200 seconds in the future -> not needed yet
    assert provider.needs_refresh(
        {"refresh_token": "abc", "token_expires_at": int(time.time()) + 200}
    ) is False
    
    # 100 seconds in the future -> within 120s buffer -> refresh needed
    assert provider.needs_refresh(
        {"refresh_token": "abc", "token_expires_at": int(time.time()) + 100}
    ) is True
    
    # Expired -> refresh needed
    assert provider.needs_refresh(
        {"refresh_token": "abc", "token_expires_at": int(time.time()) - 100}
    ) is True


def test_reddit_provider_refresh_credentials(monkeypatch):
    import time
    provider = RedditProvider(client_id="cid", client_secret="csec")
    
    def mock_request(method, url, **kwargs):
        assert method == "POST"
        assert url == "https://www.reddit.com/api/v1/access_token"
        assert kwargs["auth"] == ("cid", "csec")
        assert kwargs["data"]["grant_type"] == "refresh_token"
        assert kwargs["data"]["refresh_token"] == "old_refresh"
        
        return httpx.Response(
            200,
            json={"access_token": "new_bot_token", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )
        
    monkeypatch.setattr(provider._client, "request", mock_request)
    
    old_creds = {
        "refresh_token": "old_refresh",
        "bot_token": "old_bot_token",
        "token_expires_at": 0,
    }
    new_creds = provider.refresh_credentials(old_creds)
    
    assert new_creds["refresh_token"] == "old_refresh"
    assert new_creds["bot_token"] == "new_bot_token"
    assert new_creds["token_expires_at"] > time.time()
