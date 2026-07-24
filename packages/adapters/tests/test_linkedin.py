"""LinkedIn adapter: organization posts/comments with signed inbound parsing."""

import hashlib
import hmac
import json
import urllib.parse

import httpx
import pytest
from caspian_adapters import Settings, build_providers
from caspian_adapters.base import (
    Capability,
    OutboundMessage,
    ProvisionRequest,
    WebhookVerificationError,
)
from caspian_adapters.linkedin import (
    COMMENT_PAGE_SIZE,
    DEFAULT_VERSION,
    FakeLinkedInProvider,
    LinkedInProvider,
    encode_provider_message_id,
    parse_comments_page,
)

ACCESS_TOKEN = "linkedin-access-token"
ORG = "urn:li:organization:5637409"
POST = "urn:li:ugcPost:70161431162413057"
COMMENT = "urn:li:comment:(urn:li:activity:6631349431612559360,6636062862760562688)"
SECRET = "linkedin-client-secret"


def _provider(handler, **kwargs) -> LinkedInProvider:
    provider = LinkedInProvider(
        access_token=ACCESS_TOKEN,
        organization_urn=ORG,
        tracked_posts=POST,
        webhook_secret=SECRET,
        **kwargs,
    )
    provider._client = httpx.Client(
        base_url="https://api.linkedin.com",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    return provider


def _comment(
    *,
    comment_id: str = "6636062862760562688",
    comment_urn: str = COMMENT,
    actor: str = "urn:li:person:f49f2kf0",
    text: str = "Can someone from support answer this?",
    created_at: int = 1_582_160_678_569,
    object_urn: str = POST,
) -> dict:
    return {
        "actor": actor,
        "commentUrn": comment_urn,
        "created": {"actor": actor, "time": created_at},
        "id": comment_id,
        "message": {"attributes": [], "text": text},
        "object": object_urn,
    }


def _comments_page(*comments: dict, organization_urn: str = ORG, post_urn: str = POST) -> dict:
    return {
        "organizationUrn": organization_urn,
        "postUrn": post_urn,
        "elements": list(comments),
    }


def _signed_headers(payload: bytes, secret: str = SECRET) -> dict:
    signature = hmac.new(
        secret.encode(),
        b"hmacsha256=" + payload,
        hashlib.sha256,
    ).hexdigest()
    return {"X-LI-Signature": signature}


def test_capabilities_are_honest_for_linkedin_surface():
    assert LinkedInProvider.capabilities == {
        Capability.RECEIVE,
        Capability.REPLY,
        Capability.SEND,
    }
    assert Capability.INITIATE not in LinkedInProvider.capabilities
    assert Capability.BACKFILL not in LinkedInProvider.capabilities
    assert LinkedInProvider.connect_credentials == ("access_token", "organization_urn")
    assert LinkedInProvider.optional_connect_credentials == (
        "tracked_posts",
        "webhook_secret",
    )


def test_parse_comments_page_normalizes_linkedin_comment_json():
    inbound = parse_comments_page(_comments_page(_comment()), ORG)

    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.external_event_id == COMMENT
    assert msg.provider_inbox_id == ORG
    assert msg.provider_message_id == encode_provider_message_id(
        POST,
        "6636062862760562688",
        COMMENT,
    )
    assert msg.provider_thread_id == POST
    assert msg.sender_address == "urn:li:person:f49f2kf0"
    assert msg.subject == POST
    assert msg.text == "Can someone from support answer this?"
    assert msg.chat_type == "linkedin_comment"


def test_parse_comments_page_skips_own_echoes_and_textless_comments():
    own = _comment(actor=ORG, text="agent reply")
    textless = _comment(comment_id="2", comment_urn="", text="")

    assert parse_comments_page(_comments_page(own, textless), ORG) == []


def test_parse_webhook_accepts_valid_signature_and_rejects_bad_signature():
    provider = LinkedInProvider(organization_urn=ORG, webhook_secret=SECRET)
    payload = json.dumps(_comments_page(_comment())).encode()

    inbound = provider.parse_webhook(payload, _signed_headers(payload))
    assert [msg.text for msg in inbound] == ["Can someone from support answer this?"]

    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        provider.parse_webhook(payload, _signed_headers(payload, secret="wrong"))


def test_parse_webhook_requires_configured_secret():
    provider = LinkedInProvider(organization_urn=ORG)
    payload = json.dumps(_comments_page(_comment())).encode()

    with pytest.raises(WebhookVerificationError, match="secret missing"):
        provider.parse_webhook(payload, {})


def test_route_key_returns_organization_urn():
    assert LinkedInProvider.route_key(json.dumps(_comments_page()).encode()) == ORG
    assert LinkedInProvider.route_key(b"not json") is None
    assert LinkedInProvider.route_key(b"[]") is None
    assert LinkedInProvider.route_key(b"{}") is None


def test_send_creates_organization_post_with_rest_headers():
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        assert request.url.path == "/rest/posts"
        return httpx.Response(201, headers={"x-restli-id": POST})

    provider = _provider(handler)
    result = provider.send(ORG, OutboundMessage(text="New update"), credentials=None)

    assert seen["headers"]["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert seen["headers"]["linkedin-version"] == DEFAULT_VERSION
    assert seen["headers"]["x-restli-protocol-version"] == "2.0.0"
    assert seen["body"]["author"] == ORG
    assert seen["body"]["commentary"] == "New update"
    assert seen["body"]["visibility"] == "PUBLIC"
    assert result.provider_message_id == POST
    assert result.provider_thread_id == POST


def test_reply_to_inbound_comment_creates_nested_comment():
    new_comment_urn = (
        "urn:li:comment:(urn:li:activity:6631349431612559360,6643206422739898368)"
    )
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            headers={"x-restli-id": "6643206422739898368"},
            json={"commentUrn": new_comment_urn},
        )

    provider = _provider(handler)
    result = provider.reply(
        ORG,
        encode_provider_message_id(POST, "6636062862760562688", COMMENT),
        OutboundMessage(text="Thanks, we are checking."),
        credentials=None,
    )

    encoded_comment = urllib.parse.quote(COMMENT, safe="")
    assert seen["url"].endswith(f"/rest/socialActions/{encoded_comment}/comments")
    assert seen["body"] == {
        "actor": ORG,
        "object": POST,
        "message": {"text": "Thanks, we are checking."},
        "parentComment": COMMENT,
    }
    assert result.provider_message_id == encode_provider_message_id(
        POST,
        "6643206422739898368",
        new_comment_urn,
    )
    assert result.provider_thread_id == POST


def test_poll_comments_baselines_then_returns_new_comments_oldest_first():
    pages = [
        _comments_page(
            _comment(comment_id="101", comment_urn="urn:li:comment:(a,101)", created_at=101),
            _comment(comment_id="100", comment_urn="urn:li:comment:(a,100)", created_at=100),
        ),
        _comments_page(
            _comment(comment_id="103", comment_urn="urn:li:comment:(a,103)", created_at=103),
            _comment(comment_id="102", comment_urn="urn:li:comment:(a,102)", created_at=102),
            _comment(comment_id="101", comment_urn="urn:li:comment:(a,101)", created_at=101),
        ),
    ]
    calls = iter(pages)

    provider = _provider(lambda request: httpx.Response(200, json=next(calls)))
    initial, cursor = provider.poll_comments(None, cursor=None)
    fresh, next_cursor = provider.poll_comments(None, cursor=cursor)

    assert initial == []
    assert cursor == "101:101"
    assert [msg.provider_message_id for msg in fresh] == [
        encode_provider_message_id(POST, "102", "urn:li:comment:(a,102)"),
        encode_provider_message_id(POST, "103", "urn:li:comment:(a,103)"),
    ]
    assert next_cursor == "103:103"


def test_poll_comments_paginates_beyond_first_linkedin_page():
    first_page = [
        _comment(
            comment_id=str(comment_id),
            comment_urn=f"urn:li:comment:(a,{comment_id})",
            created_at=comment_id,
        )
        for comment_id in range(101, 101 + COMMENT_PAGE_SIZE)
    ]
    second_page = [
        _comment(comment_id="151", comment_urn="urn:li:comment:(a,151)", created_at=151)
    ]
    seen_starts = []

    def handler(request):
        seen_starts.append(request.url.params["start"])
        assert request.url.params["count"] == str(COMMENT_PAGE_SIZE)
        if request.url.params["start"] == "0":
            return httpx.Response(200, json=_comments_page(*first_page))
        return httpx.Response(200, json=_comments_page(*second_page))

    provider = _provider(handler)
    messages, cursor = provider.poll_comments(None, cursor="150:150")

    assert seen_starts == ["0", str(COMMENT_PAGE_SIZE)]
    assert [message.provider_message_id for message in messages] == [
        encode_provider_message_id(POST, "151", "urn:li:comment:(a,151)")
    ]
    assert cursor == "151:151"


def test_poll_comments_skips_own_echo_but_advances_cursor():
    page = _comments_page(
        _comment(comment_id="202", comment_urn="urn:li:comment:(a,202)", actor=ORG, created_at=202),
        _comment(comment_id="201", comment_urn="urn:li:comment:(a,201)", created_at=201),
    )
    provider = _provider(lambda request: httpx.Response(200, json=page))

    messages, cursor = provider.poll_comments(None, cursor="200:200")

    assert [message.provider_message_id for message in messages] == [
        encode_provider_message_id(POST, "201", "urn:li:comment:(a,201)")
    ]
    assert cursor == "202:202"


def test_fake_provider_uses_realistic_comment_fixtures_and_signature():
    fake = FakeLinkedInProvider()
    comment = fake.comment_fixture(text="Does this integrate with Caspian?")
    payload = json.dumps(fake.comments_page(None, comment)).encode()

    inbound = fake.parse_webhook(payload, fake.signed_headers(payload))

    assert len(inbound) == 1
    assert inbound[0].text == "Does this integrate with Caspian?"
    assert inbound[0].provider_inbox_id == fake.organization_urn


def test_fake_provider_poll_comments_matches_real_provider_cursor_behavior():
    fake = FakeLinkedInProvider()
    old = fake.comment_fixture(comment_id="301", created_at=301)
    new = fake.comment_fixture(comment_id="302", created_at=302)
    fake.comments[fake.tracked_posts[0]] = [new, old]

    baseline, cursor = fake.poll_comments({}, cursor=None)
    fresh, next_cursor = fake.poll_comments({}, cursor=cursor)

    assert baseline == []
    assert cursor == "302:302"
    assert fresh == []
    assert next_cursor == "302:302"


def test_provision_uses_connection_credentials_when_present():
    provider = LinkedInProvider()

    result = provider.provision(
        ProvisionRequest(
            "conn",
            "cust",
            "agent",
            credentials={"organization_urn": ORG},
        )
    )

    assert result.address == "linkedin:5637409"
    assert result.provider_resource_id == ORG


def test_registry_builds_real_and_fake_linkedin_providers():
    providers = build_providers(
        Settings(
            providers="linkedin,fake-linkedin",
            linkedin_access_token=ACCESS_TOKEN,
            linkedin_organization_urn=ORG,
            linkedin_tracked_posts=POST,
            linkedin_webhook_secret=SECRET,
        )
    )

    assert providers["linkedin"].channel == "linkedin"
    assert providers["fake-linkedin"].channel == "linkedin"
