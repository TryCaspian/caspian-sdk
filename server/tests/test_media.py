"""Attachments: inbound media is populated by providers that receive files
(email/SES, Telegram), and outbound media is sent as a native file per channel."""

import base64
import json
from email import message_from_bytes, policy
from email.message import EmailMessage

import httpx
from comm_gateway.providers.base import OutboundMessage
from comm_gateway.providers.ses import SESEmailProvider
from comm_gateway.providers.slack import SlackProvider
from comm_gateway.providers.telegram import TelegramProvider


class StubSES:
    def __init__(self):
        self.sent: list[bytes] = []

    def send_email(self, Content):
        self.sent.append(Content["Raw"]["Data"])
        return {"MessageId": "ses-accept-id"}


class StubS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[Key])}


def _ses(objects=None):
    return SESEmailProvider(
        region="us-east-1", domain="example.com", s3_bucket="example-inbound-mail",
        topic_arn="arn:aws:sns:us-east-1:1:mail", verify_sns=False,
        ses_client=StubSES(), s3_client=StubS3(objects or {}),
    )


def _envelope(recipients, object_key="inbound/msg1"):
    notification = {
        "notificationType": "Received",
        "mail": {"messageId": "ses-msg-1"},
        "receipt": {"recipients": recipients,
                    "action": {"type": "S3", "bucketName": "example-inbound-mail",
                               "objectKey": object_key}},
    }
    return json.dumps({"Type": "Notification",
                       "TopicArn": "arn:aws:sns:us-east-1:1:mail",
                       "Message": json.dumps(notification)}).encode()


def _email_with_attachment() -> bytes:
    mime = EmailMessage()
    mime["From"] = "Alice <alice@example.com>"
    mime["To"] = "support-abc@example.com"
    mime["Subject"] = "Receipt attached"
    mime["Message-ID"] = "<orig-1@example.com>"
    mime.set_content("See attached receipt.")
    mime.add_attachment(b"%PDF-1.4 fake pdf bytes", maintype="application",
                        subtype="pdf", filename="receipt.pdf")
    return mime.as_bytes()


# --- inbound media ----------------------------------------------------------- #

def test_inbound_email_attachment_populates_media():
    provider = _ses({"inbound/msg1": _email_with_attachment()})
    inbound = provider.parse_webhook(_envelope(["support-abc@example.com"]), {})
    assert len(inbound) == 1
    media = inbound[0].media
    assert len(media) == 1
    att = media[0]
    assert att["name"] == "receipt.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["size"] > 0
    assert base64.b64decode(att["data"]).startswith(b"%PDF")
    assert "receipt" in inbound[0].text  # body text still parsed alongside


def test_inbound_email_without_attachment_has_empty_media():
    raw = b"""From: a@example.com\r
To: support-abc@example.com\r
Subject: hi\r
Message-ID: <m2@example.com>\r
Content-Type: text/plain\r
\r
no files here\r
"""
    provider = _ses({"inbound/msg1": raw})
    inbound = provider.parse_webhook(_envelope(["support-abc@example.com"]), {})
    assert inbound[0].media == []


def test_telegram_inbound_photo_becomes_media():
    from comm_gateway.providers.telegram import parse_update

    update = {
        "update_id": 5,
        "message": {
            "message_id": 9, "chat": {"id": 4242, "type": "private"},
            "from": {"id": 1, "username": "cust"}, "date": 1,
            "caption": "my receipt",
            "photo": [{"file_id": "small", "file_size": 100},
                      {"file_id": "big", "file_size": 9000}],
        },
    }
    msgs = parse_update(update, bot_id="777")
    assert len(msgs) == 1
    assert msgs[0].text == "my receipt"
    assert msgs[0].media == [{"file_id": "big", "mime_type": "image/jpeg", "size": 9000}]


# --- outbound media ---------------------------------------------------------- #

def _mock(provider):
    captured = {"requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        captured["requests"].append({"path": request.url.path, "body": body})
        if request.url.path.endswith("/sendPhoto") or request.url.path.endswith("/sendDocument"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 55}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    provider._client = httpx.Client(base_url=str(provider._client.base_url),
                                    transport=httpx.MockTransport(handler))
    return captured


def test_telegram_outbound_image_uses_send_photo():
    p = TelegramProvider()
    cap = _mock(p)
    p.send("bot", OutboundMessage(
        text="here you go", to=("999",),
        media=({"url": "https://x/i.png", "mime_type": "image/png"},)),
        credentials={"bot_token": "111:AAA"})
    req = cap["requests"][-1]
    assert req["path"].endswith("/sendPhoto")
    assert req["body"]["photo"] == "https://x/i.png"
    assert req["body"]["caption"] == "here you go"


def test_telegram_outbound_document_uses_send_document():
    p = TelegramProvider()
    cap = _mock(p)
    p.send("bot", OutboundMessage(
        to=("999",), media=({"url": "https://x/f.pdf", "mime_type": "application/pdf"},)),
        credentials={"bot_token": "111:AAA"})
    req = cap["requests"][-1]
    assert req["path"].endswith("/sendDocument")
    assert req["body"]["document"] == "https://x/f.pdf"


def test_email_outbound_media_becomes_mime_attachment():
    provider = _ses()
    provider.send(
        "support-abc@example.com",
        OutboundMessage(text="attached", to=("alice@example.com",),
                        media=({"data": base64.b64encode(b"hello bytes").decode(),
                                "mime_type": "text/plain", "name": "note.txt"},)),
    )
    sent = message_from_bytes(provider._ses.sent[0], policy=policy.default)
    attachments = list(sent.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "note.txt"
    assert attachments[0].get_payload(decode=True) == b"hello bytes"


def test_slack_outbound_image_adds_image_block():
    p = SlackProvider(client_id="c")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    p._client = httpx.Client(base_url=str(p._client.base_url),
                             transport=httpx.MockTransport(handler))
    p.send("app:team", OutboundMessage(
        text="pic", to=("C1",), media=({"url": "https://x/i.png", "mime_type": "image/png"},)),
        credentials={"bot_token": "xoxb"})
    kinds = [b["type"] for b in captured["body"]["blocks"]]
    assert "image" in kinds
