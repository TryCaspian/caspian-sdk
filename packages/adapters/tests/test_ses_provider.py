import json
from email import message_from_bytes, policy

import pytest
from caspian_adapters.base import OutboundMessage, ProvisionRequest, WebhookVerificationError
from caspian_adapters.ses import SESEmailProvider


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


RAW_EMAIL = b"""From: Alice <alice@example.com>\r
To: support-abc@agents.example.com\r
Subject: Where is my order?\r
Message-ID: <original-123@example.com>\r
Content-Type: text/plain; charset=utf-8\r
\r
I ordered a hoodie last week.\r
"""


def _provider(objects=None):
    return SESEmailProvider(
        region="us-east-1",
        domain="agents.example.com",
        s3_bucket="example-inbound-mail",
        topic_arn="arn:aws:sns:us-east-1:123456789012:example-mail-inbound",
        verify_sns=False,
        ses_client=StubSES(),
        s3_client=StubS3(objects or {}),
    )


def _notification_envelope(recipients, object_key="inbound/msg1"):
    notification = {
        "notificationType": "Received",
        "mail": {"messageId": "ses-msg-1"},
        "receipt": {
            "recipients": recipients,
            "action": {
                "type": "S3",
                "bucketName": "example-inbound-mail",
                "objectKey": object_key,
            },
        },
    }
    return json.dumps(
        {
            "Type": "Notification",
            "TopicArn": "arn:aws:sns:us-east-1:123456789012:example-mail-inbound",
            "Message": json.dumps(notification),
        }
    ).encode()


def test_provision_allocates_address_on_domain():
    result = _provider().provision(
        ProvisionRequest(
            connection_id="conn_1", customer_id="cus_1", agent_id="agt_1",
            display_name="Order Support",
        )
    )
    assert result.address.endswith("@agents.example.com")
    assert result.address.startswith("order-support-")
    assert result.provider_resource_id == result.address


def test_parse_inbound_notification():
    provider = _provider({"inbound/msg1": RAW_EMAIL})
    inbound = provider.parse_webhook(
        _notification_envelope(["support-abc@agents.example.com"]), {}
    )
    assert len(inbound) == 1
    email_in = inbound[0]
    assert email_in.provider_inbox_id == "support-abc@agents.example.com"
    assert email_in.external_event_id == "ses-msg-1"
    assert email_in.provider_message_id == "<original-123@example.com>"
    assert email_in.provider_thread_id == "<original-123@example.com>"
    assert email_in.sender_address == "alice@example.com"
    assert email_in.subject == "Where is my order?"
    assert "hoodie" in email_in.text


def test_thread_root_follows_references():
    raw = RAW_EMAIL.replace(
        b"Message-ID: <original-123@example.com>\r\n",
        b"Message-ID: <reply-456@example.com>\r\n"
        b"In-Reply-To: <root-1@agents.example.com>\r\n"
        b"References: <root-1@agents.example.com> <mid-2@example.com>\r\n",
    )
    provider = _provider({"inbound/msg1": raw})
    inbound = provider.parse_webhook(_notification_envelope(["support-abc@agents.example.com"]), {})
    assert inbound[0].provider_thread_id == "<root-1@agents.example.com>"


def test_reply_sets_threading_headers():
    provider = _provider()
    result = provider.reply(
        "support-abc@agents.example.com",
        "<original-123@example.com>",
        OutboundMessage(text="On its way", subject="Re: Where is my order?",
                        to=("alice@example.com",)),
    )
    sent = message_from_bytes(provider._ses.sent[0], policy=policy.default)
    assert sent["In-Reply-To"] == "<original-123@example.com>"
    assert sent["References"] == "<original-123@example.com>"
    assert sent["To"] == "alice@example.com"
    assert sent["From"] == "support-abc@agents.example.com"
    assert result.provider_message_id.endswith("@agents.example.com>")


def test_wrong_topic_rejected():
    provider = _provider({"inbound/msg1": RAW_EMAIL})
    envelope = json.loads(_notification_envelope(["support-abc@agents.example.com"]))
    envelope["TopicArn"] = "arn:aws:sns:us-east-1:999:evil"
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(json.dumps(envelope).encode(), {})


def test_non_received_notification_ignored():
    provider = _provider()
    envelope = json.dumps(
        {
            "Type": "Notification",
            "TopicArn": "arn:aws:sns:us-east-1:123456789012:example-mail-inbound",
            "Message": json.dumps({"notificationType": "Bounce"}),
        }
    ).encode()
    assert provider.parse_webhook(envelope, {}) == []
