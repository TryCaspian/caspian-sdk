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

    assert new_cursor == "at://did:plc:user1/app.bsky.feed.post/123"
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


def test_bluesky_reply_fetches_root(monkeypatch):
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
            assert kwargs["json"]["record"]["reply"]["root"]["uri"] == "root_uri"
            assert kwargs["json"]["record"]["reply"]["root"]["cid"] == "root_cid"
            return MockResponse(200, {"uri": "new_post_uri", "cid": "new_post_cid"})
        return MockResponse(404, {})

    def mock_get(self, url, **kwargs):
        if "getPosts" in url:
            return MockResponse(
                200,
                {
                    "posts": [
                        {
                            "cid": "parent_cid",
                            "record": {"reply": {"root": {"uri": "root_uri", "cid": "root_cid"}}},
                        }
                    ]
                },
            )
        return MockResponse(404, {})

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "get", mock_get)

    # Passing a bare URI (no CID) should trigger getPosts
    res = provider.reply(
        "did:plc:123", "at://did:plc:user1/app.bsky.feed.post/123", OutboundMessage(text="Reply")
    )
    assert res.provider_message_id == "new_post_uri"


def test_bluesky_watermark_logic(monkeypatch):
    provider = BlueskyProvider(handle="bot.bsky.social")

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    def mock_post(self, url, **kwargs):
        return MockResponse(200, {"accessJwt": "fake_token", "did": "did:plc:123"})

    def mock_get(self, url, **kwargs):
        return MockResponse(
            200,
            {
                "cursor": "bsky_cursor_2",
                "notifications": [
                    {
                        "uri": "uri_new",
                        "cid": "cid1",
                        "reason": "mention",
                        "record": {"text": "hi new"},
                    },
                    {
                        "uri": "uri_old",
                        "cid": "cid2",
                        "reason": "mention",
                        "record": {"text": "hi old"},
                    },
                ],
            },
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "get", mock_get)

    # First poll with no cursor
    messages, cursor = provider.poll_mentions({})
    # The watermark should be the newest notification's URI
    assert cursor == "uri_new"
    assert len(messages) == 2

    # Second poll simulating the watermark being passed in
    messages2, cursor2 = provider.poll_mentions({}, cursor=cursor)
    # The watermark should stay uri_new since it's the first hit
    assert cursor2 == "uri_new"
    # No new messages because it breaks at uri_new
    assert len(messages2) == 0
