"""Bluesky provider tests."""

import json

import httpx
import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.bluesky import BlueskyProvider, parse_notification
from caspian_adapters.fake_bluesky import FakeBlueskyProvider

ACCOUNT_DID = "did:plc:agent123"
HUMAN_DID = "did:plc:human456"
SESSION = {
    "did": ACCOUNT_DID,
    "accessJwt": "access_token",
    "refreshJwt": "refresh_token",
    "handle": "agent.bsky.social",
}


def _provider(handler, **kw):
    provider = BlueskyProvider(**kw)
    provider._client = httpx.Client(
        base_url="https://bsky.social",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    return provider


def _creds(**over):
    base = {"handle": "agent.bsky.social", "app_password": "app_password"}
    base.update(over)
    return base


def test_parse_notification_normalizes_mention():
    fake = FakeBlueskyProvider()
    notif = fake.webhook_payload(
        author_handle="human",
        author_did=HUMAN_DID,
        text="Hello agent!",
        reason="mention",
        uri="at://did:plc:human456/app.bsky.feed.post/123",
        indexed_at="2026-07-24T12:00:00.000Z",
    )
    msg = parse_notification(notif, ACCOUNT_DID)
    assert msg.text == "Hello agent!"
    assert msg.provider_inbox_id == ACCOUNT_DID
    assert msg.provider_message_id == "at://did:plc:human456/app.bsky.feed.post/123"
    assert msg.provider_thread_id == "at://did:plc:human456/app.bsky.feed.post/123"
    assert msg.sender_address == HUMAN_DID
    assert msg.sender_name == "A Human"
    assert msg.chat_type == "bluesky_mention"
    assert msg.external_event_id == "at://did:plc:human456/app.bsky.feed.post/123:2026-07-24T12:00:00.000Z"


def test_parse_notification_normalizes_reply():
    fake = FakeBlueskyProvider()
    notif = fake.webhook_payload(reason="reply")
    notif["record"]["reply"] = {
        "root": {"uri": "at://did:plc:agent123/app.bsky.feed.post/root1", "cid": "c1"},
        "parent": {"uri": "at://did:plc:agent123/app.bsky.feed.post/parent1", "cid": "c2"}
    }
    msg = parse_notification(notif, ACCOUNT_DID)
    assert msg.chat_type == "bluesky_reply"
    assert msg.provider_thread_id == "at://did:plc:agent123/app.bsky.feed.post/root1"


def test_parse_notification_skips_own_and_other_reasons():
    fake = FakeBlueskyProvider()
    notif1 = fake.webhook_payload(author_did=ACCOUNT_DID)
    assert parse_notification(notif1, ACCOUNT_DID) is None

    notif2 = fake.webhook_payload(reason="like")
    assert parse_notification(notif2, ACCOUNT_DID) is None


def test_send_posts_record():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/xrpc/com.atproto.server.createSession":
            return httpx.Response(200, json=SESSION)
        if request.url.path == "/xrpc/com.atproto.repo.createRecord":
            assert request.headers["authorization"] == "Bearer access_token"
            body = json.loads(request.content)
            assert body["collection"] == "app.bsky.feed.post"
            assert body["record"]["text"] == "hello world"
            return httpx.Response(200, json={"uri": "at://did:plc:agent123/app.bsky.feed.post/new"})

    provider = _provider(handler)
    result = provider.send(
        ACCOUNT_DID, OutboundMessage(text="hello world", to=()), credentials=_creds()
    )
    assert len(calls) == 2
    assert result.provider_message_id == "at://did:plc:agent123/app.bsky.feed.post/new"


def test_reply_fetches_target_and_posts_record():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/xrpc/com.atproto.server.createSession":
            return httpx.Response(200, json=SESSION)
        if request.url.path == "/xrpc/app.bsky.feed.getPosts":
            assert request.url.query.decode() == "uris=at%3A%2F%2Ftarget%2Fpost"
            return httpx.Response(200, json={
                "posts": [
                    {
                        "uri": "at://target/post",
                        "cid": "target_cid",
                        "record": {
                            "reply": {
                                "root": {"uri": "at://root/post", "cid": "root_cid"},
                                "parent": {"uri": "at://parent/post", "cid": "parent_cid"},
                            }
                        }
                    }
                ]
            })
        if request.url.path == "/xrpc/com.atproto.repo.createRecord":
            body = json.loads(request.content)
            assert body["record"]["reply"]["root"]["uri"] == "at://root/post"
            assert body["record"]["reply"]["parent"]["uri"] == "at://target/post"
            return httpx.Response(200, json={"uri": "at://did:plc:agent123/app.bsky.feed.post/new"})

    provider = _provider(handler)
    result = provider.reply(
        ACCOUNT_DID,
        "at://target/post",
        OutboundMessage(text="reply text", to=()),
        credentials=_creds(),
    )
    assert len(calls) == 3
    assert result.provider_thread_id == "at://root/post"


def test_parse_webhook_enforces_token():
    provider = BlueskyProvider(webhook_token="secret")
    payload = json.dumps({"reason": "mention"}).encode()
    
    with pytest.raises(WebhookVerificationError, match="token mismatch"):
        provider.parse_webhook(payload, {"X-Bluesky-Token": "wrong"}, credentials=_creds())
        
    with pytest.raises(WebhookVerificationError, match="token mismatch"):
        provider.parse_webhook(payload, {}, credentials=_creds())


def test_parse_webhook_without_token_skips_check():
    calls = []
    
    def handler(request):
        calls.append(request)
        if request.url.path == "/xrpc/com.atproto.server.createSession":
            return httpx.Response(200, json=SESSION)
            
    provider = _provider(handler, webhook_token="")
    fake = FakeBlueskyProvider()
    payload = json.dumps(fake.webhook_payload(author_did=HUMAN_DID)).encode()
    
    inbound = provider.parse_webhook(payload, {}, credentials=_creds())
    assert inbound[0].provider_inbox_id == ACCOUNT_DID


def test_poll_notifications():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/xrpc/com.atproto.server.createSession":
            return httpx.Response(200, json=SESSION)
        if request.url.path == "/xrpc/app.bsky.notification.listNotifications":
            return httpx.Response(200, json={
                "cursor": "server_cursor",
                "notifications": [
                    FakeBlueskyProvider().webhook_payload(
                        author_did=HUMAN_DID, indexed_at="2026-07-24T12:05:00.000Z"
                    ),
                    FakeBlueskyProvider().webhook_payload(
                        author_did=HUMAN_DID, indexed_at="2026-07-24T12:01:00.000Z"
                    ),
                ]
            })

    provider = _provider(handler)
    
    # First poll returns empty and adopts newest indexed_at
    msgs, cursor = provider.poll_notifications(credentials=_creds(), cursor=None)
    assert msgs == []
    assert cursor == "2026-07-24T12:05:00.000Z"
    
    # Second poll with an old cursor returns messages newer than the cursor
    msgs, cursor = provider.poll_notifications(
        credentials=_creds(), cursor="2026-07-24T12:00:00.000Z"
    )
    assert len(msgs) == 2
    assert msgs[0].external_event_id.endswith("2026-07-24T12:01:00.000Z")
    assert msgs[1].external_event_id.endswith("2026-07-24T12:05:00.000Z")
    assert cursor == "2026-07-24T12:05:00.000Z"
