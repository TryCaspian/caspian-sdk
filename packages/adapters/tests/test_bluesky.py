"""BlueskyProvider tests using mocked AT Protocol XRPC responses."""

import json

import httpx
import pytest
from caspian_adapters import WebhookVerificationError
from caspian_adapters.base import OutboundMessage, ProvisionRequest
from caspian_adapters.bluesky import (
    CREATE_RECORD_PATH,
    LIST_NOTIFICATIONS_PATH,
    SESSION_PATH,
    BlueskyProvider,
    _decode_message_id,
    _encode_message_id,
)

AGENT_DID = "did:plc:agent123"
AGENT_HANDLE = "agent.bsky.social"
HUMAN_DID = "did:plc:human456"
HUMAN_HANDLE = "human.bsky.social"
ACCESS_TOKEN = "access-jwt"

ROOT_URI = f"at://{HUMAN_DID}/app.bsky.feed.post/root123"
ROOT_CID = "bafy-root"
PARENT_URI = f"at://{HUMAN_DID}/app.bsky.feed.post/parent456"
PARENT_CID = "bafy-parent"

WEBHOOK_SECRET = "test-bluesky-secret"
WEBHOOK_HEADERS = {
    "x-caspian-webhook-token": WEBHOOK_SECRET,
}


def _provider(handler) -> BlueskyProvider:
    """Return a Bluesky provider backed by a mocked HTTP transport."""
    provider = BlueskyProvider(base_url="https://bsky.social")
    provider._client = httpx.Client(
        base_url="https://bsky.social",
        transport=httpx.MockTransport(handler),
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


def _credentials(**overrides: str) -> dict[str, str]:
    credentials = {
        "identifier": AGENT_HANDLE,
        "app_password": "app-password",
    }
    credentials.update(overrides)
    return credentials


def _session_response(**overrides: str) -> dict[str, str]:
    session = {
        "did": AGENT_DID,
        "handle": AGENT_HANDLE,
        "accessJwt": ACCESS_TOKEN,
    }
    session.update(overrides)
    return session


def _notification(
    *,
    uri: str,
    cid: str,
    indexed_at: str,
    text: str,
    reason: str = "mention",
    author_did: str = HUMAN_DID,
    reply: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": indexed_at,
    }

    if reply is not None:
        record["reply"] = reply

    return {
        "uri": uri,
        "cid": cid,
        "reason": reason,
        "indexedAt": indexed_at,
        "author": {
            "did": author_did,
            "handle": HUMAN_HANDLE,
            "displayName": "A Human",
        },
        "record": record,
    }


def _webhook_payload(*notifications: dict) -> bytes:
    return json.dumps(
        {
            "notifications": list(notifications),
        }
    ).encode()


# --- provision ---------------------------------------------------------------


# First-time provisioning should validate credentials and return the connected account.
def test_provision_returns_connected_account():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == SESSION_PATH
        assert json.loads(request.content) == {
            "identifier": AGENT_HANDLE,
            "password": "app-password",
        }
        return httpx.Response(200, json=_session_response())

    provider = _provider(handler)

    result = provider.provision(
        _provision_request(),
    )

    assert result.address == AGENT_HANDLE
    assert result.provider_resource_id == AGENT_DID


# Provisioning should fail if Bluesky does not return a DID.
def test_provision_rejects_session_without_did():
    provider = _provider(
        lambda request: httpx.Response(
            200,
            json={
                "handle": AGENT_HANDLE,
                "accessJwt": ACCESS_TOKEN,
            },
        )
    )

    request = _provision_request()

    with pytest.raises(ValueError, match="missing did"):
        provider.provision(request)


# Provisioning should fail if Bluesky does not return a handle.
def test_provision_rejects_session_without_handle():
    provider = _provider(
        lambda request: httpx.Response(
            200,
            json={
                "did": AGENT_DID,
                "accessJwt": ACCESS_TOKEN,
            },
        )
    )

    request = _provision_request()

    with pytest.raises(ValueError, match="missing handle"):
        provider.provision(request)


# --- send --------------------------------------------------------------------


# Sending a normal post should create a Bluesky feed post.
def test_send_creates_bluesky_post():
    created_uri = f"at://{AGENT_DID}/app.bsky.feed.post/new123"
    created_cid = "bafy-created"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SESSION_PATH:
            return httpx.Response(200, json=_session_response())

        assert request.url.path == CREATE_RECORD_PATH
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"

        payload = json.loads(request.content)

        assert payload["repo"] == AGENT_DID
        assert payload["collection"] == "app.bsky.feed.post"
        assert payload["record"]["$type"] == "app.bsky.feed.post"
        assert payload["record"]["text"] == "hello Bluesky"
        assert "createdAt" in payload["record"]
        assert "reply" not in payload["record"]

        return httpx.Response(
            200,
            json={
                "uri": created_uri,
                "cid": created_cid,
            },
        )

    provider = _provider(handler)

    result = provider.send(
        AGENT_DID,
        OutboundMessage(text="hello Bluesky", to=()),
        credentials=_credentials(),
    )

    decoded = _decode_message_id(result.provider_message_id)

    assert decoded == {
        "uri": created_uri,
        "cid": created_cid,
        "root_uri": created_uri,
        "root_cid": created_cid,
    }
    assert result.provider_thread_id == result.provider_message_id


# Empty outbound messages should be rejected before making an API call.
def test_send_rejects_empty_text():
    provider = _provider(
        lambda request: pytest.fail("HTTP request should not be made"),
    )

    message = OutboundMessage(text="", to=())
    credentials = _credentials()

    with pytest.raises(ValueError, match="requires a text message"):
        provider.send(
            AGENT_DID,
            message,
            credentials=credentials,
        )


# Sending a post should fail if Bluesky does not return a URI.
def test_send_rejects_create_record_response_without_uri():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SESSION_PATH:
            return httpx.Response(200, json=_session_response())

        return httpx.Response(
            200,
            json={"cid": "bafy-created"},
        )

    provider = _provider(handler)

    message = OutboundMessage(text="hello", to=())
    credentials = _credentials()

    with pytest.raises(ValueError, match="missing uri"):
        provider.send(
            AGENT_DID,
            message,
            credentials=credentials,
        )


# --- reply -------------------------------------------------------------------


# Replies should preserve the original thread root.
def test_reply_creates_threaded_post():
    created_uri = f"at://{AGENT_DID}/app.bsky.feed.post/reply789"
    created_cid = "bafy-reply"

    parent_message_id = _encode_parent_message_id()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SESSION_PATH:
            return httpx.Response(200, json=_session_response())

        assert request.url.path == CREATE_RECORD_PATH

        payload = json.loads(request.content)

        assert payload["record"]["text"] == "thanks for the mention"
        assert payload["record"]["reply"] == {
            "root": {
                "uri": ROOT_URI,
                "cid": ROOT_CID,
            },
            "parent": {
                "uri": PARENT_URI,
                "cid": PARENT_CID,
            },
        }

        return httpx.Response(
            200,
            json={
                "uri": created_uri,
                "cid": created_cid,
            },
        )

    provider = _provider(handler)

    result = provider.reply(
        AGENT_DID,
        parent_message_id,
        OutboundMessage(text="thanks for the mention"),
        credentials=_credentials(),
    )

    decoded_message = _decode_message_id(result.provider_message_id)
    decoded_thread = _decode_message_id(result.provider_thread_id)

    assert decoded_message == {
        "uri": created_uri,
        "cid": created_cid,
        "root_uri": ROOT_URI,
        "root_cid": ROOT_CID,
    }
    assert decoded_thread == {
        "uri": ROOT_URI,
        "cid": ROOT_CID,
        "root_uri": ROOT_URI,
        "root_cid": ROOT_CID,
    }


# Invalid provider message IDs should be rejected.
def test_reply_rejects_invalid_provider_message_id():
    provider = _provider(
        lambda request: (
            httpx.Response(200, json=_session_response())
            if request.url.path == SESSION_PATH
            else pytest.fail("createRecord should not be called")
        ),
    )

    message = OutboundMessage(text="reply")
    credentials = _credentials()

    with pytest.raises(ValueError, match="invalid Bluesky provider_message_id"):
        provider.reply(
            AGENT_DID,
            "invalid-message-id",
            message,
            credentials=credentials,
        )


# --- polling -----------------------------------------------------------------


# The first polling cycle establishes a baseline and emits nothing.
def test_poll_notifications_first_poll_sets_baseline():
    notifications = [
        _notification(
            uri=PARENT_URI,
            cid=PARENT_CID,
            indexed_at="2026-07-24T10:05:00.000Z",
            text="newest historical notification",
        ),
        _notification(
            uri=ROOT_URI,
            cid=ROOT_CID,
            indexed_at="2026-07-24T10:00:00.000Z",
            text="older historical notification",
        ),
    ]

    provider = _polling_provider(notifications)

    messages, cursor = provider.poll_notifications(
        _credentials(),
        cursor=None,
    )

    assert messages == []
    assert cursor == "2026-07-24T10:05:00.000Z"


# Polling should ignore unsupported notification types and return new messages oldest-first.
def test_poll_notifications_returns_only_new_supported_messages_in_order():
    older_cursor = "2026-07-24T10:00:00.000Z"

    notifications = [
        _notification(
            uri=f"at://{HUMAN_DID}/app.bsky.feed.post/third",
            cid="bafy-third",
            indexed_at="2026-07-24T10:03:00.000Z",
            text="third",
            reason="reply",
        ),
        _notification(
            uri=f"at://{HUMAN_DID}/app.bsky.feed.post/ignored-like",
            cid="bafy-like",
            indexed_at="2026-07-24T10:04:00.000Z",
            text="ignored",
            reason="like",
        ),
        _notification(
            uri=f"at://{HUMAN_DID}/app.bsky.feed.post/second",
            cid="bafy-second",
            indexed_at="2026-07-24T10:02:00.000Z",
            text="second",
            reason="mention",
        ),
        _notification(
            uri=f"at://{HUMAN_DID}/app.bsky.feed.post/already-seen",
            cid="bafy-seen",
            indexed_at=older_cursor,
            text="already seen",
        ),
    ]

    provider = _polling_provider(notifications)

    messages, cursor = provider.poll_notifications(
        _credentials(),
        cursor=older_cursor,
    )

    assert [message.text for message in messages] == ["second", "third"]

    assert cursor == "2026-07-24T10:04:00.000Z"


# Notifications generated by the connected account should not be surfaced as inbound messages.
def test_poll_notifications_skips_self_authored_notifications():
    notifications = [
        _notification(
            uri=f"at://{AGENT_DID}/app.bsky.feed.post/own-post",
            cid="bafy-own",
            indexed_at="2026-07-24T10:02:00.000Z",
            text="the agent's own notification",
            author_did=AGENT_DID,
        ),
        _notification(
            uri=f"at://{HUMAN_DID}/app.bsky.feed.post/human-post",
            cid="bafy-human",
            indexed_at="2026-07-24T10:01:00.000Z",
            text="a real mention",
        ),
    ]

    provider = _polling_provider(notifications)

    messages, cursor = provider.poll_notifications(
        _credentials(),
        cursor="2026-07-24T10:00:00.000Z",
    )

    assert [message.text for message in messages] == ["a real mention"]
    assert cursor == "2026-07-24T10:02:00.000Z"


# Replies should preserve the original thread root.
def test_poll_notification_preserves_reply_root():
    reply_reference = {
        "root": {
            "uri": ROOT_URI,
            "cid": ROOT_CID,
        },
        "parent": {
            "uri": PARENT_URI,
            "cid": PARENT_CID,
        },
    }

    reply_uri = f"at://{HUMAN_DID}/app.bsky.feed.post/reply123"
    reply_cid = "bafy-inbound-reply"

    notifications = [
        _notification(
            uri=reply_uri,
            cid=reply_cid,
            indexed_at="2026-07-24T10:01:00.000Z",
            text="thread reply",
            reason="reply",
            reply=reply_reference,
        ),
    ]

    provider = _polling_provider(notifications)

    messages, _ = provider.poll_notifications(
        _credentials(),
        cursor="2026-07-24T10:00:00.000Z",
    )

    assert len(messages) == 1

    message = messages[0]
    decoded_message = _decode_message_id(message.provider_message_id)
    decoded_thread = _decode_message_id(message.provider_thread_id)

    assert decoded_message == {
        "uri": reply_uri,
        "cid": reply_cid,
        "root_uri": ROOT_URI,
        "root_cid": ROOT_CID,
    }
    assert decoded_thread["uri"] == ROOT_URI
    assert decoded_thread["cid"] == ROOT_CID
    assert message.sender_address == HUMAN_HANDLE
    assert message.sender_name == "A Human"
    assert message.chat_type == "public"


# --- message IDs -------------------------------------------------------------


# Invalid provider message IDs should be rejected.
def test_decode_message_id_rejects_invalid_prefix():
    with pytest.raises(ValueError, match="invalid Bluesky provider_message_id"):
        _decode_message_id("other:value")


# Invalid payload should be rejected.
def test_decode_message_id_rejects_invalid_payload():
    with pytest.raises(ValueError, match="invalid Bluesky provider_message_id"):
        _decode_message_id("bsky:not-valid-base64")


def _encode_parent_message_id() -> str:
    """Return a valid parent provider_message_id for reply tests."""
    return _encode_message_id(
        uri=PARENT_URI,
        cid=PARENT_CID,
        root_uri=ROOT_URI,
        root_cid=ROOT_CID,
    )


def _polling_provider(
    notifications: list[dict[str, object]],
) -> BlueskyProvider:
    """Return a provider backed by mocked notification responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SESSION_PATH:
            return httpx.Response(200, json=_session_response())

        assert request.url.path == LIST_NOTIFICATIONS_PATH
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert request.url.params["limit"] == "50"
        assert set(request.url.params.get_list("reasons")) == {
            "mention",
            "reply",
        }

        return httpx.Response(
            200,
            json={"notifications": notifications},
        )

    return _provider(handler)


# --- webhook --------------------------------------------------------------


def _webhook_notification(
    *,
    reason: str = "mention",
    indexed_at: str = "2026-07-24T10:00:00.000Z",
    suffix: str = "webhook",
    text: str = "hello from webhook",
) -> dict[str, object]:
    """Return a valid Bluesky notification for webhook tests."""
    return _notification(
        uri=f"at://{HUMAN_DID}/app.bsky.feed.post/{suffix}",
        cid=f"bafy-{suffix}",
        indexed_at=indexed_at,
        text=text,
        reason=reason,
    )


def test_parse_webhook_accepts_valid_token() -> None:
    provider = BlueskyProvider(webhook_secret=WEBHOOK_SECRET)

    inbound = provider.parse_webhook(
        _webhook_payload(
            _webhook_notification(),
        ),
        WEBHOOK_HEADERS,
        credentials={
            "provider_resource_id": AGENT_DID,
        },
    )

    assert len(inbound) == 1

    message = inbound[0]

    assert message.provider_inbox_id == AGENT_DID
    assert message.text == "hello from webhook"
    assert message.sender_address == HUMAN_HANDLE
    assert message.sender_name == "A Human"
    assert message.chat_type == "public"


@pytest.mark.parametrize(
    ("payload", "headers", "credentials", "expected_error"),
    [
        (
            _webhook_payload(
                _webhook_notification(),
            ),
            {
                "x-caspian-webhook-token": "wrong-secret",
            },
            {
                "provider_resource_id": AGENT_DID,
            },
            "Bluesky webhook token mismatch",
        ),
        (
            b"{invalid",
            WEBHOOK_HEADERS,
            {
                "provider_resource_id": AGENT_DID,
            },
            "invalid Bluesky webhook payload",
        ),
        (
            json.dumps(
                {
                    "notifications": "invalid",
                }
            ).encode(),
            WEBHOOK_HEADERS,
            {
                "provider_resource_id": AGENT_DID,
            },
            "invalid Bluesky webhook payload",
        ),
        (
            _webhook_payload(
                _webhook_notification(),
            ),
            WEBHOOK_HEADERS,
            None,
            "Bluesky webhook requires a provider inbox id",
        ),
    ],
)
def test_parse_webhook_rejects_invalid_requests(
    payload: bytes,
    headers: dict[str, str],
    credentials: dict[str, str] | None,
    expected_error: str,
) -> None:
    provider = BlueskyProvider(webhook_secret=WEBHOOK_SECRET)

    with pytest.raises(
        WebhookVerificationError,
        match=expected_error,
    ):
        provider.parse_webhook(
            payload,
            headers,
            credentials=credentials,
        )


def test_parse_webhook_normalizes_supported_notifications() -> None:
    provider = BlueskyProvider(webhook_secret=WEBHOOK_SECRET)

    inbound = provider.parse_webhook(
        _webhook_payload(
            _webhook_notification(
                reason="mention",
                indexed_at="2026-07-24T10:00:00.000Z",
                suffix="mention",
                text="mention message",
            ),
            _webhook_notification(
                reason="reply",
                indexed_at="2026-07-24T10:01:00.000Z",
                suffix="reply",
                text="reply message",
            ),
            _webhook_notification(
                reason="like",
                indexed_at="2026-07-24T10:02:00.000Z",
                suffix="like",
                text="ignored like",
            ),
        ),
        WEBHOOK_HEADERS,
        credentials={
            "provider_resource_id": AGENT_DID,
        },
    )

    assert [message.text for message in inbound] == [
        "mention message",
        "reply message",
    ]
    assert all(message.provider_inbox_id == AGENT_DID for message in inbound)
    assert all(message.chat_type == "public" for message in inbound)


def test_parse_webhook_skips_self_authored_notifications() -> None:
    provider = BlueskyProvider(webhook_secret=WEBHOOK_SECRET)

    self_notification = _notification(
        uri=f"at://{AGENT_DID}/app.bsky.feed.post/self-webhook",
        cid="bafy-self-webhook",
        indexed_at="2026-07-24T10:00:00.000Z",
        text="self-authored webhook notification",
        reason="mention",
        author_did=AGENT_DID,
    )

    human_notification = _webhook_notification(
        suffix="human-webhook",
        text="human webhook notification",
    )

    inbound = provider.parse_webhook(
        _webhook_payload(
            self_notification,
            human_notification,
        ),
        WEBHOOK_HEADERS,
        credentials={
            "provider_resource_id": AGENT_DID,
        },
    )

    assert [message.text for message in inbound] == [
        "human webhook notification",
    ]


def test_parse_webhook_rejects_payload_without_configured_secret() -> None:
    provider = BlueskyProvider()

    payload = _webhook_payload(
        _webhook_notification(),
    )
    headers: dict[str, str] = {}
    credentials = {
        "provider_resource_id": AGENT_DID,
    }

    with pytest.raises(
        WebhookVerificationError,
        match="Bluesky webhook secret is not configured",
    ):
        provider.parse_webhook(
            payload,
            headers,
            credentials=credentials,
        )