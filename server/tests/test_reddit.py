"""RedditProvider tests using mocked OAuth2 + REST API responses."""

import json

import httpx
import pytest
from comm_gateway.providers.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from comm_gateway.providers.reddit import (
    COMMENT_PATH,
    ME_PATH,
    TOKEN_PATH,
    RedditProvider,
)

CLIENT_ID = "client-123"
CLIENT_SECRET = "secret-456"
REFRESH_TOKEN = "refresh-789"
ACCESS_TOKEN = "access-abc"
AGENT_USERNAME = "agent_bot"
AGENT_USER_ID = "agent1"

WEBHOOK_SECRET = "test-reddit-secret"
WEBHOOK_HEADERS = {"x-caspian-webhook-token": WEBHOOK_SECRET}


def _credentials(**overrides: str) -> dict[str, str]:
    credentials = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    credentials.update(overrides)
    return credentials


def _provision_request() -> ProvisionRequest:
    return ProvisionRequest(
        connection_id="connection-123",
        customer_id="customer-123",
        agent_id="agent-123",
        credentials=_credentials(),
    )


def _provider(api_handler, *, token_handler=None) -> RedditProvider:
    """Return a Reddit provider with mocked API + token-endpoint transports."""

    def default_token_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == TOKEN_PATH
        return httpx.Response(200, json={"access_token": ACCESS_TOKEN})

    provider = RedditProvider(
        base_url="https://oauth.reddit.com",
        token_url="https://www.reddit.com",
        webhook_secret=WEBHOOK_SECRET,
    )
    provider._client = httpx.Client(
        base_url="https://oauth.reddit.com",
        transport=httpx.MockTransport(api_handler),
        timeout=5.0,
    )
    provider._token_client = httpx.Client(
        base_url="https://www.reddit.com",
        transport=httpx.MockTransport(token_handler or default_token_handler),
        timeout=5.0,
    )
    return provider


def _unread_listing(*items: dict) -> dict:
    children = [{"kind": item.get("_kind", "t4"), "data": item} for item in items]
    return {"kind": "Listing", "data": {"children": children}}


def _message_item(
    *, name: str, author: str, body: str, created_utc: float, subject: str = "hi"
) -> dict:
    return {
        "_kind": "t4",
        "name": name,
        "author": author,
        "body": body,
        "subject": subject,
        "created_utc": created_utc,
    }


# --- provision ----------------------------------------------------------------


# Provisioning should authenticate and return the connected Reddit account.
def test_provision_returns_connected_account():
    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == ME_PATH
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        return httpx.Response(200, json={"name": AGENT_USERNAME, "id": AGENT_USER_ID})

    provider = _provider(api_handler)

    result = provider.provision(_provision_request())

    assert result.address == f"u/{AGENT_USERNAME}"
    assert result.provider_resource_id == f"t2_{AGENT_USER_ID}"


# Provisioning should fail if credentials are incomplete.
def test_provision_rejects_missing_credentials():
    provider = _provider(lambda request: httpx.Response(200, json={}))

    request = _provision_request()
    request.credentials.pop("refresh_token")

    with pytest.raises(ValueError, match="client_id, client_secret, and refresh_token"):
        provider.provision(request)


# Provisioning should fail if Reddit does not return a username.
def test_provision_rejects_response_without_username():
    provider = _provider(lambda request: httpx.Response(200, json={"id": AGENT_USER_ID}))

    with pytest.raises(ValueError, match="missing a username"):
        provider.provision(_provision_request())


# --- send / reply ---------------------------------------------------------------


# Sending into an existing thread should post a comment against the given fullname.
def test_send_posts_comment_against_existing_thing():
    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMMENT_PATH
        body = dict(httpx.QueryParams(request.content.decode()))
        assert body["thing_id"] == "t3_post1"
        assert body["text"] == "hello there"
        return httpx.Response(
            200,
            json={"json": {"data": {"things": [{"data": {"name": "t1_new1"}}]}}},
        )

    provider = _provider(api_handler)

    result = provider.send(
        "inbox-1",
        OutboundMessage(text="hello there", to=("t3_post1",)),
        credentials=_credentials(),
    )

    assert result.provider_message_id == "t1_new1"
    assert result.provider_thread_id == "t3_post1"


# Sending without a target fullname should be rejected rather than guessing one.
def test_send_requires_existing_thing_in_to():
    provider = _provider(lambda request: httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="message.to\\[0\\]"):
        provider.send("inbox-1", OutboundMessage(text="hi"), credentials=_credentials())


# Replying should post a comment against the message being answered.
def test_reply_posts_comment_against_provider_message_id():
    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMMENT_PATH
        body = dict(httpx.QueryParams(request.content.decode()))
        assert body["thing_id"] == "t4_msg1"
        return httpx.Response(
            200,
            json={"json": {"data": {"things": [{"data": {"name": "t1_new2"}}]}}},
        )

    provider = _provider(api_handler)

    result = provider.reply(
        "inbox-1",
        "t4_msg1",
        OutboundMessage(text="reply text"),
        credentials=_credentials(),
    )

    assert result.provider_message_id == "t1_new2"
    assert result.provider_thread_id == "t4_msg1"


# Replying without text should be rejected.
def test_reply_requires_text():
    provider = _provider(lambda request: httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="requires a text message"):
        provider.reply("inbox-1", "t4_msg1", OutboundMessage(), credentials=_credentials())


# --- poll_inbox -----------------------------------------------------------------


# The first poll should adopt a baseline cursor and report no messages, so a
# newly connected agent never replies to a backlog it inherited.
def test_poll_inbox_first_poll_adopts_baseline():
    item = _message_item(name="t4_msg1", author="human1", body="hi there", created_utc=100.0)
    provider = _provider(lambda request: httpx.Response(200, json=_unread_listing(item)))

    messages, cursor = provider.poll_inbox(_credentials(), cursor=None)

    assert messages == []
    assert cursor == "100.0"


# A later poll should return only messages newer than the stored cursor, oldest first.
def test_poll_inbox_returns_only_messages_newer_than_cursor():
    older = _message_item(name="t4_msg1", author="human1", body="old", created_utc=100.0)
    newer = _message_item(name="t4_msg2", author="human1", body="new", created_utc=200.0)
    provider = _provider(
        lambda request: httpx.Response(200, json=_unread_listing(older, newer))
    )

    messages, cursor = provider.poll_inbox(_credentials(), cursor="100.0")

    assert [m.provider_message_id for m in messages] == ["t4_msg2"]
    assert messages[0].text == "new"
    assert messages[0].sender_address == "human1"
    assert messages[0].chat_type == "private"
    assert cursor == "200.0"


# A comment-reply notification (kind t1) should normalize as a public chat_type.
def test_poll_inbox_normalizes_comment_reply():
    comment = {
        "_kind": "t1",
        "name": "t1_reply1",
        "author": "human2",
        "body": "nice post",
        "link_id": "t3_post1",
        "created_utc": 300.0,
    }
    provider = _provider(lambda request: httpx.Response(200, json=_unread_listing(comment)))

    messages, cursor = provider.poll_inbox(_credentials(), cursor="200.0")

    assert len(messages) == 1
    assert messages[0].chat_type == "public"
    assert messages[0].provider_thread_id == "t3_post1"
    assert cursor == "300.0"


# --- parse_webhook ---------------------------------------------------------------


def _webhook_payload(*items: dict) -> bytes:
    return json.dumps(_unread_listing(*items)).encode()


# A correctly authenticated payload should normalize into inbound messages.
def test_parse_webhook_accepts_matching_token():
    item = _message_item(name="t4_msg1", author="human1", body="hi", created_utc=100.0)
    provider = _provider(lambda request: httpx.Response(200, json={}))

    messages = provider.parse_webhook(
        _webhook_payload(item),
        WEBHOOK_HEADERS,
        credentials={"provider_resource_id": "t2_agent1"},
    )

    assert len(messages) == 1
    assert messages[0].provider_message_id == "t4_msg1"
    assert messages[0].provider_inbox_id == "t2_agent1"


# A missing/incorrect token should be rejected.
def test_parse_webhook_rejects_missing_token():
    provider = _provider(lambda request: httpx.Response(200, json={}))

    with pytest.raises(WebhookVerificationError, match="token mismatch"):
        provider.parse_webhook(_webhook_payload(), {}, credentials={})


def test_parse_webhook_rejects_wrong_token():
    provider = _provider(lambda request: httpx.Response(200, json={}))

    with pytest.raises(WebhookVerificationError, match="token mismatch"):
        provider.parse_webhook(
            _webhook_payload(), {"x-caspian-webhook-token": "wrong"}, credentials={}
        )


# A provider with no configured webhook secret should refuse to verify at all.
def test_parse_webhook_requires_configured_secret():
    provider = RedditProvider(webhook_secret="")

    with pytest.raises(WebhookVerificationError, match="not configured"):
        provider.parse_webhook(_webhook_payload(), WEBHOOK_HEADERS, credentials={})


# Malformed JSON should be rejected as a verification error, not raise an
# unrelated exception.
def test_parse_webhook_rejects_invalid_json():
    provider = _provider(lambda request: httpx.Response(200, json={}))

    with pytest.raises(WebhookVerificationError, match="invalid Reddit webhook payload"):
        provider.parse_webhook(b"not json", WEBHOOK_HEADERS, credentials={})
