"""Streaming (post + edit): adapter methods and StreamHandle context manager.

TelegramProvider is the reference implementation: stream_send posts a
placeholder and returns a composite id; stream_edit patches it in place.
Platforms without STREAM_EDIT fall back to a single reply at the end.
"""

import json

import httpx
import pytest
from caspian_adapters.telegram import TelegramProvider
from caspian_sdk.client import StreamHandle

# ---------------------------------------------------------------------------
# TelegramProvider: stream_send and stream_edit
# ---------------------------------------------------------------------------

def test_telegram_stream_send_posts_message_and_returns_composite_id():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    p = TelegramProvider(webhook_base="", base_url="https://tg.test")
    p._client = httpx.Client(
        base_url="https://tg.test",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    composite_id = p.stream_send(
        provider_inbox_id="bot123",
        chat_id="99",
        text="…",
        credentials={"bot_token": "123:ABC"},
    )
    assert seen["path"] == "/bot123:ABC/sendMessage"
    assert seen["body"] == {"chat_id": "99", "text": "…"}
    assert composite_id == "99:42"


def test_telegram_stream_edit_patches_message_text():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    p = TelegramProvider(webhook_base="", base_url="https://tg.test")
    p._client = httpx.Client(
        base_url="https://tg.test",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    p.stream_edit(
        provider_message_id="99:42",
        text="Hello world",
        credentials={"bot_token": "123:ABC"},
    )
    assert seen["path"] == "/bot123:ABC/editMessageText"
    assert seen["body"] == {"chat_id": "99", "message_id": 42, "text": "Hello world"}


def test_telegram_has_stream_edit_capability():
    from caspian_adapters.base import Capability
    p = TelegramProvider(webhook_base="")
    assert Capability.STREAM_EDIT in p.capabilities


# ---------------------------------------------------------------------------
# StreamHandle: three behaviours
# ---------------------------------------------------------------------------

def test_chunks_accumulate_and_edit_called_with_full_buffer():
    """edit_fn should see the full accumulated text after each append."""
    edits = []
    sent = []

    handle = StreamHandle(
        send_fn=lambda text: (sent.append(text), "chat:1")[1],
        edit_fn=lambda msg_id, text: edits.append((msg_id, text)),
    )
    with handle as s:
        s.append("Hello")
        s.append(", ")
        s.append("world")

    assert edits[-1] == ("chat:1", "Hello, world")
    # __exit__ does one final edit with the complete buffer
    assert edits[-1][1] == "Hello, world"


def test_fallback_path_sends_single_reply_at_exit():
    """No edit_fn: reply_fn should be called exactly once, after the block."""
    replies = []

    handle = StreamHandle(
        send_fn=lambda text: None,
        edit_fn=None,
        reply_fn=lambda text: replies.append(text),
    )
    with handle as s:
        s.append("one")
        s.append(" two")
        # reply_fn should NOT have been called yet
        assert replies == []

    assert replies == ["one two"]


def test_error_mid_stream_flushes_partial_buffer_and_reraises():
    """__exit__ fires on exception, sends partial output + marker, re-raises."""
    sent = []

    handle = StreamHandle(
        send_fn=lambda text: None,
        edit_fn=None,
        reply_fn=lambda text: sent.append(text),
    )
    with pytest.raises(ValueError, match="LLM crashed"):
        with handle as s:
            s.append("partial output")
            raise ValueError("LLM crashed")

    # Partial output preserved; error marker appended
    assert len(sent) == 1
    assert sent[0].startswith("partial output")
    assert "[stream interrupted]" in sent[0]
