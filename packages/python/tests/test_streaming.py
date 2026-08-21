"""Streaming: send a long reply as it is produced, not after the handler ends."""

from __future__ import annotations

import json

from caspian import Caspian
from caspian.interpreters.transport import RecordingTransport


def _update(text: str = "hi", chat: int = 555) -> bytes:
    return json.dumps(
        {
            "update_id": 1,
            "message": {
                "message_id": 9,
                "from": {"id": 42},
                "chat": {"id": chat, "type": "private"},
                "text": text,
            },
        }
    ).encode()


def _cx(transport):
    cx = Caspian(transport=transport)
    cx.channels.add(
        "telegram", via="self-host", bot_token="123:ABC", webhook_secret="s3cr3t"
    )
    return cx


class TestStreamingLive:
    """With a transport and an edit-capable channel, chunks go out as they arrive."""

    def test_posts_once_then_edits(self) -> None:
        rec = RecordingTransport()
        cx = _cx(rec)

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx) -> None:  # noqa: ANN001
            with thread.stream(min_chars=1) as out:
                for chunk in ("Hello", " there", " friend"):
                    out.append(chunk)

        cx.handle(
            "telegram",
            _update(),
            {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )

        methods = [s.raw.get("native") or s.raw.get("url", "").rsplit("/", 1)[-1]
                   for s in rec.dispatched]
        sends = [m for m in methods if "sendMessage" in str(m)]
        edits = [m for m in methods if "editMessageText" in str(m)]
        assert len(sends) == 1, f"expected one initial post, got {methods}"
        assert len(edits) >= 1, f"expected edits after the post, got {methods}"

    def test_final_text_is_complete(self) -> None:
        rec = RecordingTransport()
        cx = _cx(rec)

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx) -> None:  # noqa: ANN001
            with thread.stream(min_chars=1) as out:
                out.append("alpha ")
                out.append("beta")

        cx.handle(
            "telegram",
            _update(),
            {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
        texts = [s.raw.get("json", {}).get("text") for s in rec.dispatched]
        assert "alpha beta" in [t for t in texts if t], texts

    def test_stream_reports_live(self) -> None:
        rec = RecordingTransport()
        cx = _cx(rec)
        seen = {}

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx) -> None:  # noqa: ANN001
            out = thread.stream()
            seen["live"] = out.live
            out.append("x")
            out.close()

        cx.handle(
            "telegram",
            _update(),
            {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
        assert seen["live"] is True


class TestStreamingDegrades:
    """No transport (or a channel that cannot edit) must still deliver the text."""

    def test_buffers_into_one_post_without_transport(self) -> None:
        cx = Caspian(dispatch=False)
        cx.channels.add(
        "telegram", via="self-host", bot_token="123:ABC", webhook_secret="s3cr3t"
    )
        captured = {}

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx) -> None:  # noqa: ANN001
            with thread.stream(min_chars=1) as out:
                assert out.live is False
                out.append("one ")
                out.append("two")
            captured["cmds"] = [c.tag for c in thread.commands]
            captured["text"] = [getattr(c, "text", None) for c in thread.commands]

        cx.handle(
            "telegram",
            _update(),
            {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
        assert captured["cmds"].count("Post") == 1
        assert "one two" in captured["text"]

    def test_empty_stream_sends_nothing(self) -> None:
        cx = Caspian(dispatch=False)
        cx.channels.add(
        "telegram", via="self-host", bot_token="123:ABC", webhook_secret="s3cr3t"
    )
        captured = {}

        @cx.on_message({"channel": "telegram"})
        def handler(thread, msg, ctx) -> None:  # noqa: ANN001
            with thread.stream():
                pass  # nothing appended
            captured["n"] = len(thread.commands)

        cx.handle(
            "telegram",
            _update(),
            {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
        assert captured["n"] == 0
