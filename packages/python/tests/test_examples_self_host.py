import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

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


def test_sms_help_plans_send() -> None:
    from examples.sms.app import register

    rec = RecordingTransport()
    cx = Caspian(transport=rec)
    auth_token = "token"
    webhook_url = "https://example.com/sms"
    cx.channels.add(
        "sms",
        via="self-host",
        account_sid="AC123",
        auth_token=auth_token,
        from_number="+15559876543",
        webhook_url=webhook_url,
        bot_token="local",
    )
    register(cx)
    form = {"From": ["+1"], "Body": ["/help"]}
    body = urlencode(form, doseq=True).encode()
    payload = webhook_url + "Body/helpFrom+1"
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()
    results = cx.handle("sms", body, {"X-Twilio-Signature": signature})
    assert any(r.is_ok and r.value.raw.get("transport") == "http_form" for r in results)
