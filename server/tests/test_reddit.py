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
    COMPOSE_PATH,
    ME_PATH,
    READ_MESSAGE_PATH,
    UNREAD_PATH,
    RedditProvider,
)

ACCOUNT_ID = "abc123"
USERNAME = "agent_bot"
ACCESS_TOKEN = "reddit-access-token"

WEBHOOK_SECRET = "test-reddit-secret"
WEBHOOK_HEADERS = {"x-caspian-webhook-token": WEBHOOK_SECRET}

TOKEN_URL = "https://token.example/access_token"


def _credentials(**overrides: str) -> dict[str, str]:
    credentials = {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "username": USERNAME,
        "password": "password",
    }
    credentials.update(overrides)
    return credentials


def _token_response(request: httpx.Request) -> httpx.Response:
    assert request.url == TOKEN_URL
    assert request.headers["user-agent"]
    body = request.content.decode()
    assert "grant_type=password" in body
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN})


def _provider(handler, *, token_handler=_token_response) -> RedditProvider:
    """Return a Reddit provider backed by mocked HTTP transports."""

    def routed(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return token_handler(request)
        return handler(request)

    provider = RedditProvider(base_url="https://oauth.example", token_url=TOKEN_URL)
    provider._client = httpx.Client(
        base_url="https://oauth.example",
        transport=httpx.MockTransport(routed),
        timeout=5.0,
    )
    provider._token_client = httpx.Client(
        transport=httpx.MockTransport(routed),
        timeout=5.0,
    )
    return provider


def _provision_request() -> ProvisionRequest:
    return ProvisionRequest(
        connection_id="connection-123",
        customer_id="customer-123",
        agent_id="agent-123",
        credentials=_credentials(),
    )


def _me_response(**overrides: str) -> dict[str, str]:
    me = {"name": USERNAME, "id": ACCOUNT_ID}
    me.update(overrides)
    return me


# --- auth ----------------------------------------------------------------


def test_authenticate_rejects_missing_credentials():
    provider = _provider(lambda request: pytest.fail("HTTP request should not be made"))

    with pytest.raises(ValueError, match="requires client_id"):
        provider.provision(
            ProvisionRequest(
                connection_id="c",
                customer_id="c",
                agent_id="a",
                credentials={"client_id": "only-one-field"},
            )
        )


def test_authenticate_rejects_failed_token_exchange():
    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    provider = _provider(
        lambda request: pytest.fail("API should not be called"),
        token_handler=token_handler,
    )

    with pytest.raises(ValueError, match="authentication failed"):
        provider.provision(_provision_request())


# --- provision -------------------------------------------------------------


def test_provision_returns_connected_account():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == ME_PATH
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        return httpx.Response(200, json=_me_response())

    provider = _provider(handler)

    result = provider.provision(_provision_request())

    assert result.address == USERNAME
    assert result.provider_resource_id == f"t2_{ACCOUNT_ID}"


def test_provision_rejects_response_without_name():
    provider = _provider(lambda request: httpx.Response(200, json={"id": ACCOUNT_ID}))

    with pytest.raises(ValueError, match="missing name"):
        provider.provision(_provision_request())


def test_provision_rejects_response_without_id():
    provider = _provider(lambda request: httpx.Response(200, json={"name": USERNAME}))

    with pytest.raises(ValueError, match="missing id"):
        provider.provision(_provision_request())


# --- send --------------------------------------------------------------------


def test_send_composes_private_message():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMPOSE_PATH
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"

        form = dict(httpx.QueryParams(request.content.decode()))
        assert form["to"] == "a_redditor"
        assert form["text"] == "hello there"
        assert form["subject"] == "Message from your agent"

        return httpx.Response(200, json={"json": {"errors": [], "data": {}}})

    provider = _provider(handler)

    result = provider.send(
        "t2_ignored",
        OutboundMessage(text="hello there", to=("a_redditor",)),
        credentials=_credentials(),
    )

    assert result.provider_message_id == "pending:a_redditor"
    assert result.provider_thread_id == "pending:a_redditor"


def test_send_rejects_empty_text():
    provider = _provider(lambda request: pytest.fail("HTTP request should not be made"))

    with pytest.raises(ValueError, match="requires a text message"):
        provider.send(
            "t2_ignored",
            OutboundMessage(text="", to=("a_redditor",)),
            credentials=_credentials(),
        )


def test_send_rejects_missing_recipient():
    provider = _provider(lambda request: pytest.fail("HTTP request should not be made"))

    with pytest.raises(ValueError, match="exactly one recipient"):
        provider.send(
            "t2_ignored",
            OutboundMessage(text="hi", to=()),
            credentials=_credentials(),
        )


def test_send_surfaces_reddit_api_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"json": {"errors": [["NOT_WHITELISTED_BY_USER_MESSAGE", "blocked", "to"]]}},
        )

    provider = _provider(handler)

    with pytest.raises(ValueError, match="compose.*returned an error"):
        provider.send(
            "t2_ignored",
            OutboundMessage(text="hi", to=("closed_user",)),
            credentials=_credentials(),
        )


# --- reply -------------------------------------------------------------------


def test_reply_comments_on_parent_message():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMMENT_PATH

        form = dict(httpx.QueryParams(request.content.decode()))
        assert form["thing_id"] == "t4_parent123"
        assert form["text"] == "thanks for reaching out"

        return httpx.Response(
            200,
            json={
                "json": {
                    "errors": [],
                    "data": {"things": [{"kind": "t1", "data": {"name": "t1_reply789"}}]},
                }
            },
        )

    provider = _provider(handler)

    result = provider.reply(
        "t2_ignored",
        "t4_parent123",
        OutboundMessage(text="thanks for reaching out"),
        credentials=_credentials(),
    )

    assert result.provider_message_id == "t1_reply789"
    assert result.provider_thread_id == "t1_reply789"


def test_reply_rejects_missing_parent_id():
    provider = _provider(lambda request: pytest.fail("HTTP request should not be made"))

    with pytest.raises(ValueError, match="requires a provider_message_id"):
        provider.reply(
            "t2_ignored",
            "",
            OutboundMessage(text="hi"),
            credentials=_credentials(),
        )


def test_reply_rejects_response_without_fullname():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"json": {"errors": [], "data": {"things": []}}},
        )

    provider = _provider(handler)

    with pytest.raises(ValueError, match="missing the reply's fullname"):
        provider.reply(
            "t2_ignored",
            "t4_parent123",
            OutboundMessage(text="hi"),
            credentials=_credentials(),
        )


# --- poll_messages -----------------------------------------------------------


def _inbox_entry(
    *,
    name: str,
    author: str = "a_redditor",
    body: str = "hello",
    created_utc: float = 1_700_000_000.0,
    subject: str = "hi",
    first_message_name: str | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "name": name,
        "author": author,
        "body": body,
        "created_utc": created_utc,
        "subject": subject,
        "was_comment": False,
    }
    if first_message_name:
        data["first_message_name"] = first_message_name
    return {"kind": "t4", "data": data}


def _listing(*entries: dict[str, object]) -> dict[str, object]:
    return {"kind": "Listing", "data": {"children": list(entries), "after": None}}


def _polling_provider(entries: list[dict[str, object]]) -> RedditProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == ME_PATH:
            return httpx.Response(200, json=_me_response())
        if request.url.path == UNREAD_PATH:
            assert request.method == "GET"
            assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
            return httpx.Response(200, json=_listing(*entries))
        if request.url.path == READ_MESSAGE_PATH:
            return httpx.Response(200, json={"json": {"errors": [], "data": {}}})
        return httpx.Response(404, json={})

    return _provider(handler)


def test_poll_messages_cold_start_returns_no_messages():
    entries = [_inbox_entry(name="t4_one", created_utc=1_700_000_100.0)]
    provider = _polling_provider(entries)

    messages, cursor = provider.poll_messages(_credentials(), cursor=None)

    assert messages == []
    assert cursor == repr(1_700_000_100.0)


def test_poll_messages_returns_only_fresh_entries():
    entries = [
        _inbox_entry(name="t4_old", created_utc=1_700_000_000.0, body="old"),
        _inbox_entry(name="t4_new", created_utc=1_700_000_200.0, body="new"),
    ]
    provider = _polling_provider(entries)

    messages, cursor = provider.poll_messages(
        _credentials(),
        cursor=repr(1_700_000_100.0),
    )

    assert [m.text for m in messages] == ["new"]
    assert messages[0].provider_message_id == "t4_new"
    assert messages[0].provider_inbox_id == f"t2_{ACCOUNT_ID}"
    assert messages[0].sender_address == "a_redditor"
    assert messages[0].chat_type == "private"
    assert cursor == repr(1_700_000_200.0)


def test_poll_messages_thread_id_falls_back_to_first_message_name():
    entries = [
        _inbox_entry(
            name="t1_reply",
            created_utc=1_700_000_300.0,
            first_message_name="t4_root",
        ),
    ]
    provider = _polling_provider(entries)

    messages, _ = provider.poll_messages(
        _credentials(),
        cursor=repr(1_700_000_200.0),
    )

    assert len(messages) == 1
    assert messages[0].provider_thread_id == "t4_root"


def test_poll_messages_marks_polled_entries_read():
    read_calls: list[str] = []
    entries = [_inbox_entry(name="t4_one", created_utc=1_700_000_400.0)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == ME_PATH:
            return httpx.Response(200, json=_me_response())
        if request.url.path == UNREAD_PATH:
            return httpx.Response(200, json=_listing(*entries))
        if request.url.path == READ_MESSAGE_PATH:
            form = dict(httpx.QueryParams(request.content.decode()))
            read_calls.append(form["id"])
            return httpx.Response(200, json={"json": {"errors": [], "data": {}}})
        return httpx.Response(404, json={})

    provider = _provider(handler)

    provider.poll_messages(_credentials(), cursor=repr(1_700_000_300.0))

    assert read_calls == ["t4_one"]


# --- webhook (offline-fake / relay symmetry path) ----------------------------


def _webhook_payload(*messages: dict) -> bytes:
    return json.dumps({"messages": list(messages)}).encode()


def _webhook_message(
    *,
    name: str = "t4_webhook",
    text: str = "hello from webhook",
    author: str = "a_redditor",
) -> dict[str, object]:
    return {
        "kind": "t4",
        "data": {
            "name": name,
            "author": author,
            "subject": "hi",
            "body": text,
            "was_comment": False,
        },
    }


def test_parse_webhook_accepts_valid_token():
    provider = RedditProvider(webhook_secret=WEBHOOK_SECRET)

    inbound = provider.parse_webhook(
        _webhook_payload(_webhook_message()),
        WEBHOOK_HEADERS,
        credentials={"provider_resource_id": f"t2_{ACCOUNT_ID}"},
    )

    assert len(inbound) == 1
    message = inbound[0]
    assert message.provider_inbox_id == f"t2_{ACCOUNT_ID}"
    assert message.text == "hello from webhook"
    assert message.sender_address == "a_redditor"
    assert message.chat_type == "private"


@pytest.mark.parametrize(
    ("payload", "headers", "credentials", "expected_error"),
    [
        (
            _webhook_payload(_webhook_message()),
            {"x-caspian-webhook-token": "wrong-secret"},
            {"provider_resource_id": f"t2_{ACCOUNT_ID}"},
            "Reddit webhook token mismatch",
        ),
        (
            b"{invalid",
            WEBHOOK_HEADERS,
            {"provider_resource_id": f"t2_{ACCOUNT_ID}"},
            "invalid Reddit webhook payload",
        ),
        (
            json.dumps({"messages": "invalid"}).encode(),
            WEBHOOK_HEADERS,
            {"provider_resource_id": f"t2_{ACCOUNT_ID}"},
            "invalid Reddit webhook payload",
        ),
        (
            _webhook_payload(_webhook_message()),
            WEBHOOK_HEADERS,
            None,
            "Reddit webhook requires a provider inbox id",
        ),
    ],
)
def test_parse_webhook_rejects_invalid_requests(payload, headers, credentials, expected_error):
    provider = RedditProvider(webhook_secret=WEBHOOK_SECRET)

    with pytest.raises(WebhookVerificationError, match=expected_error):
        provider.parse_webhook(payload, headers, credentials=credentials)


def test_parse_webhook_rejects_payload_without_configured_secret():
    provider = RedditProvider()

    with pytest.raises(WebhookVerificationError, match="webhook secret is not configured"):
        provider.parse_webhook(
            _webhook_payload(_webhook_message()),
            {},
            credentials={"provider_resource_id": f"t2_{ACCOUNT_ID}"},
        )


def test_parse_webhook_normalizes_multiple_messages():
    provider = RedditProvider(webhook_secret=WEBHOOK_SECRET)

    inbound = provider.parse_webhook(
        _webhook_payload(
            _webhook_message(name="t4_one", text="first"),
            _webhook_message(name="t4_two", text="second"),
        ),
        WEBHOOK_HEADERS,
        credentials={"provider_resource_id": f"t2_{ACCOUNT_ID}"},
    )

    assert [m.text for m in inbound] == ["first", "second"]


# --- registry ----------------------------------------------------------------


def test_registry_builds_reddit_and_fake_reddit_providers():
    from comm_gateway.config import Settings
    from comm_gateway.providers.registry import _build_one

    settings = Settings(providers="reddit")
    provider = _build_one("reddit", settings)
    assert provider.name == "reddit"
    assert provider.channel == "reddit"

    fake = _build_one("fake-reddit", Settings(providers="fake-reddit"))
    assert fake.name == "fake-reddit"
    assert fake.channel == "reddit"
