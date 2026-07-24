import httpx
from caspian_adapters.base import OutboundMessage
from caspian_adapters.bluesky import BlueskyProvider


def test_bluesky_capabilities():
    provider = BlueskyProvider()
    assert "receive" in provider.capabilities
    assert "reply" in provider.capabilities
    assert "send" in provider.capabilities


def test_bluesky_poll_mentions_mocked(monkeypatch):
    provider = BlueskyProvider(handle="bot.bsky.social")

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = "Error"

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception("HTTP Error")

    def mock_post(self, url, **kwargs):
        if "createSession" in url:
            return MockResponse(200, {"accessJwt": "fake_token", "did": "did:plc:123"})
        return MockResponse(404, {})

    def mock_get(self, url, **kwargs):
        if "listNotifications" in url:
            return MockResponse(
                200,
                {
                    "cursor": "next_page",
                    "notifications": [
                        {
                            "uri": "at://did:plc:user1/app.bsky.feed.post/123",
                            "cid": "cid1",
                            "author": {"did": "did:plc:user1", "handle": "u1.bsky"},
                            "reason": "mention",
                            "record": {
                                "text": "@bot hello",
                                "reply": {"root": {"uri": "root_uri", "cid": "root_cid"}},
                            },
                        }
                    ],
                },
            )
        return MockResponse(404, {})

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "get", mock_get)

    messages, new_cursor = provider.poll_mentions({})

    assert new_cursor == "next_page"
    assert len(messages) == 1
    msg = messages[0]
    assert msg.external_event_id == "at://did:plc:user1/app.bsky.feed.post/123"
    assert msg.provider_message_id == "at://did:plc:user1/app.bsky.feed.post/123||cid1"
    assert msg.sender_address == "did:plc:user1"
    assert msg.sender_name == "u1.bsky"
    assert msg.text == "@bot hello"
    assert msg.provider_thread_id == "root_uri"


def test_bluesky_send(monkeypatch):
    provider = BlueskyProvider(handle="bot.bsky.social")

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = "Error"

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    def mock_post(self, url, **kwargs):
        if "createSession" in url:
            return MockResponse(200, {"accessJwt": "fake_token", "did": "did:plc:123"})
        if "createRecord" in url:
            return MockResponse(200, {"uri": "new_post_uri", "cid": "new_post_cid"})
        return MockResponse(404, {})

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    res = provider.send("did:plc:123", OutboundMessage(text="Hello world"))
    assert res.provider_message_id == "new_post_uri"
