"""Tests for hosted inbound: parser, signature verifier, dedup, poller."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from caspian.core.ports import RawInbound, Result
from caspian.hosted.client import FakeGatewayClient, GatewayResponse
from caspian.hosted.inbound import (
    DedupCache,
    GatewayEventParser,
    GatewayPoller,
    GatewaySignatureVerifier,
)


def _raw(payload: dict[str, Any]) -> RawInbound:
    return RawInbound(body=json.dumps(payload).encode("utf-8"))


class TestGatewayEventParser:
    def test_message(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "id": "evt_1",
                    "type": "message",
                    "channel": "telegram",
                    "conversation_id": "conv_abc",
                    "message": {
                        "id": "m1",
                        "text": "hi",
                        "sender": "u1",
                        "chat_kind": "dm",
                        "reply_to": "m0",
                        "attachments": [
                            {"type": "photo", "url": "http://x/y.png", "file_id": "f1"}
                        ],
                    },
                }
            )
        )
        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.kind == "message"
        assert str(msg.thread_id) == "telegram:conv_abc"
        assert msg.text == "hi"
        assert msg.sender == "u1"
        assert msg.message_id == "m1"
        assert msg.reply_to == "m0"
        assert msg.chat_kind == "dm"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "photo"
        assert msg.attachments[0].url == "http://x/y.png"

    def test_action(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "id": "evt_2",
                    "type": "action",
                    "channel": "slack",
                    "conversation_id": "c9",
                    "action": {"data": "approve", "message_id": "m2"},
                }
            )
        )
        event = result.value[0]
        assert event.kind == "action"
        assert str(event.thread_id) == "slack:c9"
        assert event.data == "approve"
        assert event.message_id == "m2"

    def test_reaction(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "type": "reaction",
                    "channel": "discord",
                    "conversation_id": "c1",
                    "reaction": {"emoji": "👍", "message_id": "m3"},
                }
            )
        )
        event = result.value[0]
        assert event.kind == "reaction"
        assert str(event.thread_id) == "discord:c1"
        assert event.emoji == "👍"
        assert event.message_id == "m3"

    def test_receipt(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "type": "receipt",
                    "channel": "telegram",
                    "conversation_id": "c2",
                    "receipt": {"status": "read", "message_id": "m4"},
                }
            )
        )
        event = result.value[0]
        assert event.kind == "receipt"
        assert str(event.thread_id) == "telegram:c2"
        assert event.status == "read"
        assert event.message_id == "m4"

    def test_edited(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "type": "edited",
                    "channel": "telegram",
                    "conversation_id": "c3",
                    "edited": {"message_id": "m5", "text": "new text"},
                }
            )
        )
        event = result.value[0]
        assert event.kind == "edited"
        assert str(event.thread_id) == "telegram:c3"
        assert event.message_id == "m5"
        assert event.text == "new text"

    def test_deleted(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "type": "deleted",
                    "channel": "telegram",
                    "conversation_id": "c4",
                    "deleted": {"message_id": "m6"},
                }
            )
        )
        event = result.value[0]
        assert event.kind == "deleted"
        assert str(event.thread_id) == "telegram:c4"
        assert event.message_id == "m6"

    def test_member_events(self) -> None:
        parser = GatewayEventParser()
        join = parser.parse(
            _raw(
                {
                    "type": "member_join",
                    "channel": "telegram",
                    "conversation_id": "g1",
                    "member_join": {"member": "u2"},
                }
            )
        ).value[0]
        assert join.kind == "member_join"
        assert join.member == "u2"
        leave = parser.parse(
            _raw(
                {
                    "type": "member_leave",
                    "channel": "telegram",
                    "conversation_id": "g1",
                    "member_leave": {"member": "u3"},
                }
            )
        ).value[0]
        assert leave.kind == "member_leave"
        assert leave.member == "u3"

    def test_unknown_type_is_empty(self) -> None:
        result = GatewayEventParser().parse(
            _raw({"type": "supernova", "channel": "x", "conversation_id": "c"})
        )
        assert result.is_ok
        assert result.value == []

    def test_invalid_json_is_decode_error(self) -> None:
        result = GatewayEventParser().parse(RawInbound(body=b"{not json"))
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"

    def test_batch(self) -> None:
        result = GatewayEventParser().parse(
            _raw(
                {
                    "events": [
                        {
                            "type": "message",
                            "channel": "telegram",
                            "conversation_id": "c",
                            "message": {"id": "m1", "text": "a", "chat_kind": "dm"},
                        },
                        {
                            "type": "reaction",
                            "channel": "telegram",
                            "conversation_id": "c",
                            "reaction": {"emoji": "👍", "message_id": "m1"},
                        },
                        {"type": "unknown", "channel": "telegram", "conversation_id": "c"},
                    ]
                }
            )
        )
        assert result.is_ok
        events = result.value
        assert len(events) == 2
        assert events[0].kind == "message"
        assert events[1].kind == "reaction"


class TestGatewaySignatureVerifier:
    def test_no_secret_always_true(self) -> None:
        verifier = GatewaySignatureVerifier(secret="")
        assert verifier.verify(RawInbound(body=b"anything")) is True

    def test_correct_signature_accepted(self) -> None:
        body = b'{"type":"message"}'
        secret = "s3cr3t"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        verifier = GatewaySignatureVerifier(secret=secret)
        raw = RawInbound(body=body, headers={"X-Caspian-Signature": sig})
        assert verifier.verify(raw) is True

    def test_sha256_prefix_accepted(self) -> None:
        body = b'{"type":"message"}'
        secret = "s3cr3t"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        verifier = GatewaySignatureVerifier(secret=secret)
        raw = RawInbound(body=body, headers={"X-Caspian-Signature": f"sha256={sig}"})
        assert verifier.verify(raw) is True

    def test_wrong_signature_rejected(self) -> None:
        verifier = GatewaySignatureVerifier(secret="s3cr3t")
        raw = RawInbound(body=b"body", headers={"X-Caspian-Signature": "deadbeef"})
        assert verifier.verify(raw) is False

    def test_missing_signature_rejected(self) -> None:
        verifier = GatewaySignatureVerifier(secret="s3cr3t")
        assert verifier.verify(RawInbound(body=b"body")) is False


class TestDedupCache:
    def test_seen_record_seen(self) -> None:
        cache = DedupCache()
        assert cache.seen("evt_1") is False
        cache.record("evt_1")
        assert cache.seen("evt_1") is True

    def test_bounded_eviction(self) -> None:
        cache = DedupCache(max_size=2)
        cache.record("a")
        cache.record("b")
        cache.record("c")
        assert cache.seen("a") is False
        assert cache.seen("b") is True
        assert cache.seen("c") is True

    def test_record_at_capacity_does_not_crash(self) -> None:
        cache = DedupCache(max_size=4)
        for i in range(100):
            cache.record(f"evt_{i}")
        assert cache.seen("evt_99") is True
        assert cache.seen("evt_0") is False

    def test_duplicate_record_is_idempotent(self) -> None:
        cache = DedupCache(max_size=2)
        cache.record("a")
        cache.record("a")
        cache.record("b")
        assert cache.seen("a") is True
        assert cache.seen("b") is True


class TestGatewayPoller:
    def test_poll_returns_events_and_updates_cursor(self) -> None:
        """Real shape: a bare array of EventOut, paged by integer seq."""
        client = FakeGatewayClient()
        client.queue(
            Result.ok(
                GatewayResponse(
                    status_code=200,
                    json_list=[{
                        "id": "evt_1", "seq": 7, "type": "message.received",
                        "data": {"message": {
                            "id": "msg_1", "conversation_id": "conv_1",
                            "channel": "slack", "direction": "inbound",
                            "text": "hi", "chat_type": "channel",
                        }},
                    }],
                )
            )
        )
        poller = GatewayPoller(client, replay=True)
        result = poller.poll()
        assert result.is_ok
        assert len(result.value) == 1
        assert poller.cursor == 7

    def test_poll_sends_after_seq_not_cursor(self) -> None:
        client = FakeGatewayClient()
        client.queue(Result.ok(GatewayResponse(status_code=200, json_list=[])))
        GatewayPoller(client, replay=True, cursor="12").poll()
        assert client.requests[-1].params == {"after_seq": "12", "limit": "100"}

    def test_poll_propagates_error(self) -> None:
        client = FakeGatewayClient()
        client.queue_status(401, "unauthorized")
        poller = GatewayPoller(client, replay=True)
        result = poller.poll()
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AuthRequired"
