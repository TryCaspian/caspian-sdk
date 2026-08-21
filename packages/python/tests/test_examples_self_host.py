import json
import sys
from pathlib import Path

from caspian import Caspian
from caspian.core.types import Message, ThreadId
from caspian.interpreters.transport import RecordingTransport

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_discord_help_posts_menu() -> None:
    from examples.discord.app import register

    cx = Caspian(dispatch=False)
    register(cx)
    event = Message(
        thread_id=ThreadId("discord:1"),
        text="/help",
        chat_kind="channel",
    )
    result = cx.interpret().run(cx.app, event, channel_name="discord")
    assert any(getattr(c, "tag", "") == "Host" for c in result.commands)


def test_slack_help_posts_menu() -> None:
    from examples.slack.app import register

    cx = Caspian(dispatch=False)
    register(cx)
    event = Message(thread_id=ThreadId("slack:C1"), text="/help", chat_kind="channel")
    result = cx.interpret().run(cx.app, event, channel_name="slack")
    assert any(getattr(c, "tag", "") == "Host" for c in result.commands)


def test_email_help_plans_smtp() -> None:
    from examples.email.app import register

    rec = RecordingTransport()
    cx = Caspian(transport=rec)
    cx.channels.add(
        "email",
        via="self-host",
        from_address="bot@example.com",
        bot_token="local",
    )
    register(cx)
    body = json.dumps(
        {
            "from": "a@b.c",
            "to": "bot@example.com",
            "subject": "x",
            "body": "/help",
            "message_id": "<1>",
        }
    ).encode()
    results = cx.handle("email", body, {})
    assert any(r.is_ok and r.value.raw.get("transport") == "smtp" for r in results)
