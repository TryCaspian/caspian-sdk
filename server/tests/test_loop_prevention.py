import json

from comm_gateway.providers.ses import SESEmailProvider
from comm_gateway.routes.api import CONVERSATION_DAILY_REPLY_CAP


class StubSES:
    def __init__(self):
        self.sent = []

    def send_email(self, Content):
        self.sent.append(Content["Raw"]["Data"])
        return {"MessageId": "ses-accept-id"}


class StubS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[Key])}


def _ses_provider(objects):
    return SESEmailProvider(
        region="us-east-1",
        domain="example.com",
        s3_bucket="example-inbound-mail",
        verify_sns=False,
        ses_client=StubSES(),
        s3_client=StubS3(objects),
    )


def _envelope(object_key="inbound/msg1"):
    notification = {
        "notificationType": "Received",
        "mail": {"messageId": "ses-auto-1"},
        "receipt": {
            "recipients": ["support-abc@example.com"],
            "action": {"type": "S3", "bucketName": "example-inbound-mail", "objectKey": object_key},
        },
    }
    return json.dumps({"Type": "Notification", "Message": json.dumps(notification)}).encode()


def test_ses_detects_auto_generated_headers():
    raw = (
        b"From: Vacation Bot <someone@example.com>\r\n"
        b"To: support-abc@example.com\r\n"
        b"Subject: Out of office\r\n"
        b"Message-ID: <auto-1@example.com>\r\n"
        b"Auto-Submitted: auto-replied\r\n"
        b"\r\nI am away.\r\n"
    )
    inbound = _ses_provider({"inbound/msg1": raw}).parse_webhook(_envelope(), {})
    assert inbound[0].auto_generated is True


def test_ses_detects_mailer_daemon_sender():
    raw = (
        b"From: MAILER-DAEMON@mail.example.com\r\n"
        b"To: support-abc@example.com\r\n"
        b"Subject: Delivery failure\r\n"
        b"Message-ID: <bounce-1@example.com>\r\n"
        b"\r\nDelivery failed.\r\n"
    )
    inbound = _ses_provider({"inbound/msg1": raw}).parse_webhook(_envelope(), {})
    assert inbound[0].auto_generated is True


def test_ses_normal_mail_not_flagged():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: support-abc@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Message-ID: <normal-1@example.com>\r\n"
        b"\r\nHi.\r\n"
    )
    inbound = _ses_provider({"inbound/msg1": raw}).parse_webhook(_envelope(), {})
    assert inbound[0].auto_generated is False


def _connect_and_deliver(app, client, run_jobs, **kwargs):
    connection = client.post("/v1/connections/email", json={"display_name": "Guard"}).json()
    run_jobs()
    provider = app.state.providers["fake"]
    inbox_id = next(iter(provider.inboxes))
    payload = provider.webhook_payload(inbox_id, **kwargs)
    client.post("/internal/providers/fake/webhooks", json=payload)
    run_jobs()
    return connection


def test_auto_generated_message_gets_no_event(app, client, run_jobs):
    connection = _connect_and_deliver(app, client, run_jobs)
    provider = app.state.providers["fake"]
    inbox_id = next(iter(provider.inboxes))
    payload = provider.webhook_payload(inbox_id, subject="OOO")
    payload["message"]["auto_generated"] = True
    client.post("/internal/providers/fake/webhooks", json=payload)
    run_jobs()

    events = client.get("/v1/events", params={"type": "message.received"}).json()
    subjects = [e["data"]["message"]["subject"] for e in events]
    assert "OOO" not in subjects
    assert connection["id"]


def test_cannot_reply_to_auto_generated(app, client, run_jobs):
    _connect_and_deliver(app, client, run_jobs)
    provider = app.state.providers["fake"]
    inbox_id = next(iter(provider.inboxes))
    payload = provider.webhook_payload(inbox_id, subject="Bounce notice")
    payload["message"]["auto_generated"] = True
    client.post("/internal/providers/fake/webhooks", json=payload)
    run_jobs()

    conversations = client.get("/v1/conversations").json()
    target = None
    for conversation in conversations:
        for message in client.get(f"/v1/conversations/{conversation['id']}/messages").json():
            if message["subject"] == "Bounce notice":
                target = message
    assert target is not None and target["auto_generated"] is True

    response = client.post(f"/v1/messages/{target['id']}/reply", json={"text": "hi"})
    assert response.status_code == 400
    assert "loop prevention" in response.json()["detail"]


def test_conversation_daily_reply_cap(app, client, run_jobs):
    _connect_and_deliver(app, client, run_jobs, subject="Chatty", thread_id="cap-thread")
    events = client.get("/v1/events", params={"type": "message.received"}).json()
    inbound_id = events[-1]["data"]["message"]["id"]

    for _ in range(CONVERSATION_DAILY_REPLY_CAP):
        reply = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "ok"})
        assert reply.status_code == 201
    response = client.post(f"/v1/messages/{inbound_id}/reply", json={"text": "one too many"})
    assert response.status_code == 429
