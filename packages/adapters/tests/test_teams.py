"""Microsoft Teams adapter: activity normalization and JWT accept/reject."""

import json

import pytest
from caspian_adapters.base import OutboundMessage, WebhookVerificationError
from caspian_adapters.fake_teams import FakeTeamsProvider
from caspian_adapters.teams import (
    TeamsProvider,
    _pack_thread,
    _unpack_thread,
    parse_activity,
    verify_activity_jwt,
)

APP_ID = "11111111-1111-1111-1111-111111111111"


def _activity(**overrides):
    base = {
        "type": "message",
        "id": "activity-1",
        "timestamp": "2026-07-25T00:00:00.000Z",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": "19:abc@thread.tacv2", "conversationType": "personal"},
        "from": {"id": "29:user-1", "name": "Alice"},
        "text": "hi there",
    }
    base.update(overrides)
    return base


# -- thread packing --


def test_pack_unpack_thread_round_trips():
    packed = _pack_thread("19:abc@thread.tacv2", "https://smba.trafficmanager.net/amer/")
    conversation_id, service_url = _unpack_thread(packed)
    assert conversation_id == "19:abc@thread.tacv2"
    assert service_url == "https://smba.trafficmanager.net/amer/"


# -- normalization --


def test_parse_activity_normalizes_message():
    inbound = parse_activity(_activity(), APP_ID)
    assert len(inbound) == 1
    msg = inbound[0]
    assert msg.text == "hi there"
    assert msg.provider_inbox_id == APP_ID
    assert msg.sender_address == "29:user-1"
    assert msg.sender_name == "Alice"
    assert msg.chat_type == "personal"
    conversation_id, service_url = _unpack_thread(msg.provider_thread_id)
    assert conversation_id == "19:abc@thread.tacv2"
    assert service_url == "https://smba.trafficmanager.net/amer/"


def test_parse_activity_marks_channel_as_group():
    inbound = parse_activity(_activity(conversation={
        "id": "19:abc@thread.tacv2", "conversationType": "channel",
    }), APP_ID)
    assert inbound[0].chat_type == "group"


def test_parse_activity_skips_non_message_and_textless():
    assert parse_activity(_activity(type="conversationUpdate"), APP_ID) == []
    assert parse_activity(_activity(text=None), APP_ID) == []
    assert parse_activity(_activity(text=""), APP_ID) == []


# -- JWT verification (real RSA signatures, offline JWKS) --


def test_verify_activity_jwt_accepts_valid_token():
    fake = FakeTeamsProvider()
    token = fake.sign_activity_jwt(APP_ID)
    claims = verify_activity_jwt(token, APP_ID, fake.jwks())
    assert claims["aud"] == APP_ID


def test_verify_activity_jwt_rejects_wrong_audience():
    fake = FakeTeamsProvider()
    token = fake.sign_activity_jwt("some-other-app-id")
    with pytest.raises(WebhookVerificationError, match="audience"):
        verify_activity_jwt(token, APP_ID, fake.jwks())


def test_verify_activity_jwt_rejects_wrong_issuer():
    fake = FakeTeamsProvider()
    token = fake.sign_activity_jwt(APP_ID, issuer="https://not-bot-framework.example")
    with pytest.raises(WebhookVerificationError, match="issuer"):
        verify_activity_jwt(token, APP_ID, fake.jwks())


def test_verify_activity_jwt_rejects_expired_token():
    fake = FakeTeamsProvider()
    token = fake.sign_activity_jwt(APP_ID, expires_in=-10)
    with pytest.raises(WebhookVerificationError, match="expired"):
        verify_activity_jwt(token, APP_ID, fake.jwks())


def test_verify_activity_jwt_rejects_signature_from_a_different_key():
    fake = FakeTeamsProvider()
    other = FakeTeamsProvider()
    token = other.sign_activity_jwt(APP_ID)  # signed with a different keypair
    with pytest.raises(WebhookVerificationError, match="signature"):
        verify_activity_jwt(token, APP_ID, fake.jwks())


def test_verify_activity_jwt_rejects_malformed_token():
    fake = FakeTeamsProvider()
    with pytest.raises(WebhookVerificationError):
        verify_activity_jwt("not-a-jwt", APP_ID, fake.jwks())


# -- FakeTeamsProvider.parse_webhook end-to-end (real verify + real normalize) --


def test_fake_provider_parse_webhook_accepts_signed_activity():
    fake = FakeTeamsProvider()
    token = fake.sign_activity_jwt(fake.app_id)
    payload = json.dumps(fake.activity_payload(text="hello")).encode()
    inbound = fake.parse_webhook(payload, {"Authorization": f"Bearer {token}"})
    assert inbound[0].text == "hello"


def test_fake_provider_parse_webhook_rejects_missing_bearer():
    fake = FakeTeamsProvider()
    payload = json.dumps(fake.activity_payload()).encode()
    with pytest.raises(WebhookVerificationError, match="bearer"):
        fake.parse_webhook(payload, {})


def test_fake_provider_parse_webhook_rejects_bad_signature():
    fake = FakeTeamsProvider()
    forger = FakeTeamsProvider()
    token = forger.sign_activity_jwt(fake.app_id)  # not fake's own key
    payload = json.dumps(fake.activity_payload()).encode()
    with pytest.raises(WebhookVerificationError, match="signature"):
        fake.parse_webhook(payload, {"Authorization": f"Bearer {token}"})


# -- send/reply round trip against the fake --


def test_fake_provider_send_and_reply_round_trip():
    fake = FakeTeamsProvider()
    thread = _pack_thread("19:abc@thread.tacv2", "https://smba.trafficmanager.net/amer/")
    sent = fake.send("app", OutboundMessage(text="hello", to=(thread,)))
    assert sent.provider_thread_id == thread
    replied = fake.reply("app", sent.provider_message_id, OutboundMessage(text="hi back"))
    assert replied.provider_thread_id == thread
    assert fake.replies[0]["in_reply_to"] == sent.provider_message_id.rsplit(":", 1)[1]


# -- real TeamsProvider with an injected (offline) JWKS fetcher --


def test_teams_provider_parse_webhook_with_injected_jwks_no_network():
    fake = FakeTeamsProvider()  # only used to mint a real keypair + token
    provider = TeamsProvider(jwks_fetcher=fake.jwks)
    token = fake.sign_activity_jwt(APP_ID)
    payload = json.dumps(_activity()).encode()
    inbound = provider.parse_webhook(
        payload, {"Authorization": f"Bearer {token}"}, credentials={"app_id": APP_ID}
    )
    assert inbound[0].text == "hi there"


def test_teams_provider_parse_webhook_requires_connection_scope():
    provider = TeamsProvider(jwks_fetcher=lambda: {"keys": []})
    with pytest.raises(WebhookVerificationError, match="connection scope"):
        provider.parse_webhook(b"{}", {"Authorization": "Bearer x"}, credentials=None)