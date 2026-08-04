"""RedditProvider: reactive + first-touch private messages via OAuth2, with
mocked HTTP. Reddit has no webhook for PMs, so inbound is covered by
poll_inbox rather than parse_webhook (which is expected to always raise).
"""

import httpx
import pytest
from comm_gateway.providers.base import (
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from comm_gateway.providers.reddit import RedditProvider

CLIENT_ID = "client123"
CLIENT_SECRET = "secret456"
REFRESH_TOKEN = "refresh-abc"
ACCESS_TOKEN = "access-xyz"


def _provider(handler, auth_handler=None, **kw):
    """A RedditProvider whose API + token clients are backed by mock transports."""
    provider = RedditProvider(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, **kw)
    provider._client = httpx.Client(
        base_url=provider._base_url,
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    provider._auth_client = httpx.Client(
        base_url=provider._auth_url,
        transport=httpx.MockTransport(auth_handler or _default_auth_handler),
        timeout=5.0,
    )
    return provider


def _default_auth_handler(request):
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})


def _creds(**over):
    base = {"refresh_token": REFRESH_TOKEN}
    base.update(over)
    return base


# --- token exchange -----------------------------------------------------------

def test_access_token_is_exchanged_and_used_as_bearer():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"name": "agentbot", "id": "t2_abc"})

    provider = _provider(handler)
    provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    assert seen["auth"] == f"Bearer {ACCESS_TOKEN}"


def test_access_token_request_uses_client_credentials_auth():
    seen = {}

    def auth_handler(request):
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})

    provider = _provider(lambda r: httpx.Response(200, json={"name": "a", "id": "t2_a"}),
                         auth_handler=auth_handler)
    provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    # httpx encodes basic auth for us; just confirm one was sent and the
    # refresh_token grant was in the body.
    assert seen["authorization"] is not None
    assert b"grant_type=refresh_token" in seen["body"]
    assert REFRESH_TOKEN.encode() in seen["body"]


def test_access_token_is_cached_across_calls():
    calls = []

    def auth_handler(request):
        calls.append(request)
        return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})

    provider = _provider(
        lambda r: httpx.Response(200, json={"name": "a", "id": "t2_a"}), auth_handler=auth_handler
    )
    provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    assert len(calls) == 1  # second call reused the cached token


def test_access_token_requires_refresh_token_credential():
    provider = _provider(lambda r: httpx.Response(404))
    with pytest.raises(ValueError):
        provider.provision(ProvisionRequest("c", "cust", "agt", credentials={}))


# --- provision -----------------------------------------------------------------

def test_provision_returns_username_and_id():
    provider = _provider(lambda r: httpx.Response(200, json={"name": "agentbot", "id": "t2_abc"}))
    result = provider.provision(ProvisionRequest("c", "cust", "agt", credentials=_creds()))
    assert result.address == "u/agentbot"
    assert result.provider_resource_id == "t2_abc"


# --- send: /api/compose ---------------------------------------------------------

def test_send_composes_a_new_message():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/compose"
        return httpx.Response(200, json={"json": {"errors": []}})

    provider = _provider(handler)
    provider.send(
        "t2_abc",
        OutboundMessage(text="hi there", subject="Question", to=("someuser",)),
        credentials=_creds(),
    )
    assert len(calls) == 1
    assert calls[0].url.path == "/api/compose"


def test_send_defaults_subject_when_missing():
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"json": {"errors": []}})

    provider = _provider(handler)
    provider.send("t2_abc", OutboundMessage(text="hi", to=("someuser",)), credentials=_creds())
    assert "subject=Message" in seen["body"]


# --- reply: /api/comment (shared with PM replies on Reddit) --------------------

def test_reply_hits_comment_endpoint_with_parent_fullname():
    def handler(request):
        assert request.url.path == "/api/comment"
        body = request.content.decode()
        assert "thing_id=t4_original" in body
        return httpx.Response(
            200, json={"json": {"data": {"things": [{"data": {"id": "newreply1"}}]}}}
        )

    provider = _provider(handler)
    result = provider.reply(
        "t2_abc", "t4_original", OutboundMessage(text="thanks!"), credentials=_creds()
    )
    assert result.provider_message_id == "t1_newreply1"
    assert result.provider_thread_id == "t4_original"


# --- poll_inbox (no-webhook inbound path) ---------------------------------------

def _inbox_page(*messages):
    """A GET /message/inbox response body from (id, author, subject, body) tuples."""
    return {
        "data": {
            "children": [
                {
                    "kind": "t4",
                    "data": {"id": mid, "author": author, "subject": subj, "body": body,
                             "dest": "agentbot"},
                }
                for mid, author, subj, body in messages
            ]
        }
    }


def test_poll_inbox_first_poll_sets_baseline_emits_nothing():
    page = _inbox_page(("300", "human1", "hi", "old message"), ("299", "human1", "hi", "older"))
    provider = _provider(lambda r: httpx.Response(200, json=page))
    msgs, cursor = provider.poll_inbox(_creds(), cursor=None)
    assert msgs == []
    assert cursor == "t4_300"


def test_poll_inbox_returns_only_new_in_order():
    page = _inbox_page(
        ("303", "human1", "s3", "third"),
        ("302", "human1", "s2", "second"),
        ("301", "human1", "s1", "already seen"),
    )
    provider = _provider(lambda r: httpx.Response(200, json=page))
    msgs, cursor = provider.poll_inbox(_creds(), cursor="t4_301")
    assert [m.text for m in msgs] == ["second", "third"]  # oldest-first
    assert cursor == "t4_303"
    assert msgs[0].provider_message_id == "t4_302"
    assert msgs[0].sender_address == "human1"
    assert msgs[0].chat_type == "reddit_dm"


def test_poll_inbox_empty_inbox_keeps_cursor():
    provider = _provider(lambda r: httpx.Response(200, json=_inbox_page()))
    msgs, cursor = provider.poll_inbox(_creds(), cursor="t4_100")
    assert msgs == []
    assert cursor == "t4_100"


# --- parse_webhook: unsupported on this channel --------------------------------

def test_parse_webhook_always_raises():
    # Reddit has no webhook delivery for PMs; this documents that clearly
    # rather than silently returning an empty list.
    provider = _provider(lambda r: httpx.Response(404))
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(b"{}", {})
