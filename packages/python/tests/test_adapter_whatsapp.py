"""Tests for the WhatsApp Cloud API adapter."""

from __future__ import annotations

import hashlib
import hmac
import json

from caspian.adapters.whatsapp import WhatsAppAdapter
from caspian.core.commands import Delete, Post, React, Reply, SendMedia
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import Attachment, Button, ConnectionId, Message, ThreadId


def _conn() -> Connection:
    return Connection(
        id=ConnectionId("wa1"),
        channel="whatsapp",
        config={"access_token": "TKN", "phone_number_id": "111222"},
    )


def _messages_webhook(message: dict) -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {"field": "messages", "value": {"messages": [message]}}
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


class TestWhatsAppParse:
    def test_parse_text_message(self) -> None:
        adapter = WhatsAppAdapter()
        raw = RawInbound(
            body=_messages_webhook(
                {
                    "from": "15551234567",
                    "id": "wamid.ABC",
                    "type": "text",
                    "text": {"body": "hello"},
                }
            )
        )
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        assert events[0].kind == "message"
        assert events[0].text == "hello"
        assert events[0].thread_id == "whatsapp:15551234567"
        assert events[0].chat_kind == "dm"
        assert events[0].message_id == "wamid.ABC"
        assert events[0].sender == "15551234567"

    def test_parse_reaction_message(self) -> None:
        adapter = WhatsAppAdapter()
        raw = RawInbound(
            body=_messages_webhook(
                {
                    "from": "15551234567",
                    "id": "wamid.R",
                    "type": "reaction",
                    "reaction": {"message_id": "wamid.ABC", "emoji": "👍"},
                }
            )
        )
        result = adapter.parse(raw)

        assert result.is_ok
        event = result.value[0]
        assert event.kind == "reaction"
        assert event.emoji == "👍"
        assert event.message_id == "wamid.ABC"

    def test_parse_status_receipt(self) -> None:
        adapter = WhatsAppAdapter()
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {
                                        "id": "wamid.OUT",
                                        "status": "read",
                                        "recipient_id": "15551234567",
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        result = adapter.parse(RawInbound(body=json.dumps(payload).encode()))

        assert result.is_ok
        event = result.value[0]
        assert event.kind == "receipt"
        assert event.status == "read"
        assert event.thread_id == "whatsapp:15551234567"

    def test_parse_unknown_returns_empty(self) -> None:
        adapter = WhatsAppAdapter()
        result = adapter.parse(RawInbound(body=json.dumps({"foo": "bar"}).encode()))
        assert result.is_ok
        assert result.value == []

    def test_parse_invalid_json_returns_error(self) -> None:
        adapter = WhatsAppAdapter()
        result = adapter.parse(RawInbound(body=b"not json"))
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "DecodeError"


class TestWhatsAppExecute:
    def test_execute_post(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = Post(thread_id=ThreadId("whatsapp:15551234567"), text="hi")
        result = adapter.execute(cmd, _conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["method"] == "POST"
        assert sent.raw["url"] == (
            "https://graph.facebook.com/v21.0/111222/messages"
        )
        assert sent.raw["native"] == "text"
        assert sent.raw["headers"]["Authorization"] == "Bearer TKN"
        body = sent.raw["json"]
        assert body["messaging_product"] == "whatsapp"
        assert body["to"] == "15551234567"
        assert body["type"] == "text"
        assert body["text"]["body"] == "hi"

    def test_execute_post_with_buttons(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = Post(
            thread_id=ThreadId("whatsapp:15551234567"),
            text="pick",
            actions=(
                Button(label="Yes", data="y"),
                Button(label="No", data="n"),
            ),
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        body = result.value.raw["json"]
        assert body["type"] == "interactive"
        buttons = body["interactive"]["action"]["buttons"]
        assert len(buttons) == 2
        assert buttons[0]["reply"] == {"id": "y", "title": "Yes"}

    def test_execute_reply_sets_context(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = Reply(
            thread_id=ThreadId("whatsapp:15551234567"),
            reply_to="wamid.ABC",
            text="re",
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        assert result.value.raw["json"]["context"] == {"message_id": "wamid.ABC"}

    def test_execute_react(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = React(
            thread_id=ThreadId("whatsapp:15551234567"),
            message_id="wamid.ABC",
            emoji="❤️",
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        sent = result.value
        assert sent.raw["native"] == "reaction"
        body = sent.raw["json"]
        assert body["type"] == "reaction"
        assert body["reaction"] == {"message_id": "wamid.ABC", "emoji": "❤️"}

    def test_execute_send_media(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = SendMedia(
            thread_id=ThreadId("whatsapp:15551234567"),
            attachment=Attachment(type="photo", url="https://x/y.png"),
            caption="look",
        )
        result = adapter.execute(cmd, _conn())
        assert result.is_ok
        body = result.value.raw["json"]
        assert body["type"] == "image"
        assert body["image"]["link"] == "https://x/y.png"
        assert body["image"]["caption"] == "look"

    def test_execute_without_token_errors(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = Post(thread_id=ThreadId("whatsapp:15551234567"), text="hi")
        conn = Connection(id=ConnectionId("c"), channel="whatsapp", config={})
        result = adapter.execute(cmd, conn)
        assert not result.is_ok
        assert result.error is not None
        assert "access_token" in result.error.reason

    def test_execute_delete_unsupported(self) -> None:
        adapter = WhatsAppAdapter()
        cmd = Delete(thread_id=ThreadId("whatsapp:15551234567"), message_id="x")
        result = adapter.execute(cmd, _conn())
        assert not result.is_ok
        assert result.error is not None
        assert result.error.command_tag == "Delete"


class TestWhatsAppVerify:
    def test_verify_ok_when_no_secret(self) -> None:
        adapter = WhatsAppAdapter()
        conn = Connection(id=ConnectionId("c"), channel="whatsapp", config={})
        assert adapter.verify(RawInbound(body=b"{}"), conn) is True

    def test_verify_checks_signature(self) -> None:
        adapter = WhatsAppAdapter()
        conn = Connection(
            id=ConnectionId("c"),
            channel="whatsapp",
            config={"app_secret": "shh"},
        )
        body = b'{"entry":[]}'
        digest = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        good = RawInbound(
            body=body, headers={"X-Hub-Signature-256": "sha256=" + digest}
        )
        bad = RawInbound(body=body, headers={"X-Hub-Signature-256": "sha256=nope"})
        assert adapter.verify(good, conn) is True
        assert adapter.verify(bad, conn) is False


class TestWhatsAppMisc:
    def test_overlap_key(self) -> None:
        adapter = WhatsAppAdapter()
        event = Message(
            thread_id=ThreadId("whatsapp:15551234567"),
            text="hi",
            chat_kind="dm",
        )
        assert adapter.overlap_key(event) == "whatsapp:15551234567"

    def test_capabilities(self) -> None:
        adapter = WhatsAppAdapter()
        assert adapter.capabilities() == frozenset(
            {"receive", "reply", "send", "media", "buttons", "react", "receipts"}
        )

    def test_thread_roundtrip(self) -> None:
        adapter = WhatsAppAdapter()
        tid = adapter.encode_thread("15551234567")
        assert tid == "whatsapp:15551234567"
        assert adapter.decode_thread(tid) == "15551234567"
