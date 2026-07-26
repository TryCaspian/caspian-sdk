import json
import pytest

from comm_gateway.providers.bluesky import BlueskyProvider
from comm_gateway.providers.base import ProvisionRequest, OutboundMessage


@pytest.fixture
def provider():
    return BlueskyProvider(identifier="test_user", password="test_password")


def test_provision(httpx_mock, provider):
    httpx_mock.add_response(
        method="POST",
        url="https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"handle": "test.bsky.social", "did": "did:plc:test123", "accessJwt": "token"},
    )
    result = provider.provision(ProvisionRequest(connection_id="1", customer_id="c1", agent_id="a1"))
    assert result.address == "test.bsky.social"
    assert result.provider_resource_id == "did:plc:test123"


def test_send_post(httpx_mock, provider):
    httpx_mock.add_response(
        method="POST",
        url="https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"handle": "test.bsky.social", "did": "did:plc:test123", "accessJwt": "token"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://bsky.social/xrpc/com.atproto.repo.createRecord",
        json={"uri": "at://did:plc:test123/app.bsky.feed.post/123", "cid": "bafy"},
    )
    result = provider.send("did:plc:test123", OutboundMessage(text="Hello world"))
    assert result.provider_thread_id == "at://did:plc:test123/app.bsky.feed.post/123"
    assert "bafy" in result.provider_message_id


def test_poll_dms(httpx_mock, provider):
    httpx_mock.add_response(
        method="POST",
        url="https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"handle": "test.bsky.social", "did": "did:plc:test123", "accessJwt": "token"},
    )
    notifications_data = {
        "notifications": [
            {
                "uri": "at://did:plc:other/app.bsky.feed.post/456",
                "cid": "bafyother",
                "author": {"did": "did:plc:other", "handle": "other.bsky.social"},
                "reason": "mention",
                "record": {"text": "Hello @test.bsky.social"},
                "indexedAt": "2023-11-20T19:30:22.123Z",
            }
        ]
    }
    httpx_mock.add_response(
        method="GET",
        url="https://bsky.social/xrpc/app.bsky.notification.listNotifications?limit=50",
        json=notifications_data,
    )
    
    messages, new_cursor = provider.poll_dms(None, cursor="2023-11-20T19:30:00.000Z")
    assert len(messages) == 1
    msg = messages[0]
    assert msg.sender_address == "did:plc:other"
    assert msg.sender_name == "other.bsky.social"
    assert msg.text == "Hello @test.bsky.social"
    assert new_cursor == "2023-11-20T19:30:22.123Z"
