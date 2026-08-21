"""Tests for the Twilio Voice adapter."""

from __future__ import annotations

from urllib.parse import urlencode

from caspian.adapters.voice import VoiceAdapter
from caspian.core.commands import Post, React, Reply
from caspian.core.ports import Connection, RawInbound
from caspian.core.types import ConnectionId, Message, ThreadId


def _inbound(fields: dict[str, str]) -> RawInbound:
    return RawInbound(body=urlencode(fields).encode())


class TestVoiceParse:
    """adapter.parse turns Twilio voice form bytes into kernel Events."""

    def test_parse_call_form(self) -> None:
        adapter = VoiceAdapter()
        raw = _inbound(
            {
                "CallSid": "CA123",
                "From": "+15551234567",
                "To": "+15559876543",
                "SpeechResult": "book a table",
            }
        )
        result = adapter.parse(raw)

        assert result.is_ok
        events = result.value
        assert len(events) == 1
        msg = events[0]
        assert msg.kind == "message"
        assert msg.text == "book a table"
        assert msg.thread_id == "voice:CA123"
        assert msg.sender == "+15551234567"
        assert msg.chat_kind == "dm"

    def test_parse_no_call_sid_returns_empty(self) -> None:
        adapter = VoiceAdapter()
        result = adapter.parse(_inbound({"From": "+15551234567"}))
        assert result.is_ok
        assert result.value == []


class TestVoiceExecute:
    """adapter.execute turns Commands into TwiML."""

    def _conn(self) -> Connection:
        return Connection(id=ConnectionId("c1"), channel="voice", config={})

    def test_execute_post_produces_twiml_say(self) -> None:
        adapter = VoiceAdapter()
        cmd = Post(thread_id=ThreadId("voice:CA123"), text="hello caller")
        result = adapter.execute(cmd, self._conn())

        assert result.is_ok
        sent = result.value
        assert sent.raw["transport"] == "twiml"
        twiml = sent.raw["twiml"]
        assert "<Say>hello caller</Say>" in twiml
        assert twiml.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_execute_reply_produces_twiml(self) -> None:
        adapter = VoiceAdapter()
        cmd = Reply(thread_id=ThreadId("voice:CA123"), reply_to="", text="ok")
        result = adapter.execute(cmd, self._conn())
        assert result.is_ok
        assert "<Say>ok</Say>" in result.value.raw["twiml"]

    def test_execute_escapes_text(self) -> None:
        adapter = VoiceAdapter()
        cmd = Post(thread_id=ThreadId("voice:CA123"), text="a & b < c")
        result = adapter.execute(cmd, self._conn())
        twiml = result.value.raw["twiml"]
        assert "a &amp; b &lt; c" in twiml

    def test_execute_react_errors(self) -> None:
        adapter = VoiceAdapter()
        cmd = React(thread_id=ThreadId("voice:CA123"), message_id="CA1", emoji="👍")
        result = adapter.execute(cmd, self._conn())

        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"
        assert result.error.command_tag == "React"


class TestVoiceMisc:
    def test_overlap_key(self) -> None:
        adapter = VoiceAdapter()
        event = Message(
            thread_id=ThreadId("voice:CA123"), text="", chat_kind="dm"
        )
        assert adapter.overlap_key(event) == "voice:CA123"

    def test_capabilities(self) -> None:
        adapter = VoiceAdapter()
        assert adapter.capabilities() == frozenset(
            {"receive", "send", "voice", "tts"}
        )
