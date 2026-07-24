"""AWS SES adapter - we own the mail.

Addresses live on our domain; provisioning is just an allocation. Inbound:
SES receipt rule stores raw MIME in S3 and publishes SNS to our webhook.
Outbound: raw MIME via SES with RFC 5322 threading headers. The provider
thread id is the root Message-ID of the References chain.
"""

import base64
import json
import re
import secrets
import uuid
from collections.abc import Mapping
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from urllib.parse import urlparse

import httpx

from .base import (
    Capability,
    InboundEvent,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)

_CERT_CACHE: dict[str, bytes] = {}

_SIGNED_FIELDS = {
    "Notification": ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"],
    "SubscriptionConfirmation": [
        "Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type",
    ],
    "UnsubscribeConfirmation": [
        "Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type",
    ],
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "agent"


_AUTO_SENDER_LOCALS = {
    "mailer-daemon", "postmaster", "no-reply", "noreply", "donotreply", "do-not-reply", "bounce",
}


def _is_auto_generated(parsed, sender_address: str | None) -> bool:
    auto_submitted = str(parsed.get("Auto-Submitted", "")).strip().lower()
    if auto_submitted and auto_submitted != "no":
        return True
    if parsed.get("X-Auto-Response-Suppress") or parsed.get("X-Autoreply"):
        return True
    precedence = str(parsed.get("Precedence", "")).strip().lower()
    if precedence in {"bulk", "auto_reply", "junk", "list"}:
        return True
    if sender_address:
        local = sender_address.split("@", 1)[0].lower()
        if local in _AUTO_SENDER_LOCALS:
            return True
    return False


def _verify_sns_signature(envelope: dict) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509 import load_pem_x509_certificate

    cert_url = envelope.get("SigningCertURL", "")
    parsed = urlparse(cert_url)
    if parsed.scheme != "https" or not re.fullmatch(
        r"sns\.[a-z0-9-]+\.amazonaws\.com", parsed.hostname or ""
    ):
        raise WebhookVerificationError("untrusted SNS signing cert URL")
    if cert_url not in _CERT_CACHE:
        _CERT_CACHE[cert_url] = httpx.get(cert_url, timeout=15).content
    certificate = load_pem_x509_certificate(_CERT_CACHE[cert_url])

    fields = _SIGNED_FIELDS.get(envelope.get("Type", ""))
    if fields is None:
        raise WebhookVerificationError("unknown SNS message type")
    canonical = "".join(
        f"{key}\n{envelope[key]}\n" for key in fields if envelope.get(key) is not None
    )
    algorithm = (
        hashes.SHA256() if envelope.get("SignatureVersion") == "2" else hashes.SHA1()
    )
    try:
        certificate.public_key().verify(
            base64.b64decode(envelope["Signature"]),
            canonical.encode(),
            padding.PKCS1v15(),
            algorithm,
        )
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise WebhookVerificationError("SNS signature verification failed") from exc


class SESEmailProvider:
    name = "ses"
    channel = "email"
    capabilities = frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND, Capability.INITIATE}
    )

    def __init__(
        self,
        region: str,
        domain: str,
        s3_bucket: str,
        topic_arn: str = "",
        verify_sns: bool = True,
        rule_set: str = "",
        rule_name: str = "",
        ses_client=None,
        s3_client=None,
        sesv1_client=None,
    ) -> None:
        if not domain:
            raise ValueError("COMM_SES_DOMAIN is required for the ses provider")
        self._region = region
        self._domain = domain
        self.default_domain = domain  # platform domain used when no custom domain
        self._bucket = s3_bucket
        self._topic_arns = {t.strip() for t in topic_arn.split(",") if t.strip()}
        self._verify_sns = verify_sns
        self._rule_set = rule_set
        self._rule_name = rule_name
        if ses_client is None or s3_client is None or sesv1_client is None:
            import boto3

            ses_client = ses_client or boto3.client("sesv2", region_name=region)
            s3_client = s3_client or boto3.client("s3", region_name=region)
            sesv1_client = sesv1_client or boto3.client("ses", region_name=region)
        self._ses = ses_client
        self._s3 = s3_client
        self._sesv1 = sesv1_client

    # Outbound

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        local = request.username or (
            f"{_slug(request.display_name or request.agent_id)}-{secrets.token_hex(3)}"
        )
        address = f"{local}@{request.domain or self._domain}"
        return ProvisionResult(address=address, provider_resource_id=address)

    # Custom domains

    def create_domain(self, domain: str) -> list[dict]:
        """Register a customer domain with SES and return the DNS records to add."""
        try:
            response = self._ses.create_email_identity(EmailIdentity=domain)
            tokens = response["DkimAttributes"]["Tokens"]
        except self._ses.exceptions.AlreadyExistsException:
            identity = self._ses.get_email_identity(EmailIdentity=domain)
            tokens = identity["DkimAttributes"]["Tokens"]
        records = [
            {
                "type": "CNAME",
                "name": f"{token}._domainkey.{domain}",
                "value": f"{token}.dkim.amazonses.com",
            }
            for token in tokens
        ]
        records.append(
            {
                "type": "MX",
                "name": domain,
                "value": f"inbound-smtp.{self._region}.amazonaws.com",
                "priority": 10,
            }
        )
        return records

    def check_domain(self, domain: str) -> bool:
        identity = self._ses.get_email_identity(EmailIdentity=domain)
        return identity["DkimAttributes"]["Status"] == "SUCCESS"

    def enable_inbound(self, domain: str) -> None:
        """Add the domain to the active receipt rule so its mail reaches us."""
        if not (self._rule_set and self._rule_name):
            return
        rule = self._sesv1.describe_receipt_rule(
            RuleSetName=self._rule_set, RuleName=self._rule_name
        )["Rule"]
        if domain in rule.get("Recipients", []):
            return
        rule["Recipients"] = [*rule.get("Recipients", []), domain]
        self._sesv1.update_receipt_rule(RuleSetName=self._rule_set, Rule=rule)

    def _send_mime(
        self,
        from_address: str,
        to: list[str],
        subject: str | None,
        text: str | None,
        html: str | None,
        in_reply_to: str | None = None,
    ) -> str:
        if not to:
            raise ValueError("no recipients")
        mime = EmailMessage()
        mime["From"] = from_address
        mime["To"] = ", ".join(to)
        if subject:
            mime["Subject"] = subject
        message_id = f"<{uuid.uuid4().hex}@{self._domain}>"
        mime["Message-ID"] = message_id
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to
            mime["References"] = in_reply_to
        mime.set_content(text or "")
        if html:
            mime.add_alternative(html, subtype="html")
        self._ses.send_email(Content={"Raw": {"Data": mime.as_bytes()}})
        return message_id

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        message_id = self._send_mime(
            provider_inbox_id, list(message.to), message.subject, message.text, message.html
        )
        return SendResult(provider_message_id=message_id, provider_thread_id=message_id)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        message_id = self._send_mime(
            provider_inbox_id,
            list(message.to),
            message.subject,
            message.text,
            message.html,
            in_reply_to=provider_message_id,
        )
        return SendResult(provider_message_id=message_id)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        message_id = self._send_mime(
            provider_inbox_id, [recipient], message.subject, message.text, message.html
        )
        return SendResult(provider_message_id=message_id, provider_thread_id=message_id)

    def send_test_email(
        self, provider_inbox_id: str, to_address: str, subject: str, text: str
    ) -> None:
        self._send_mime(f"tester@{self._domain}", [to_address], subject, text, None)
        return None

    # Inbound

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundEvent]:
        try:
            envelope = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid SNS payload") from exc
        if self._verify_sns:
            _verify_sns_signature(envelope)
        if self._topic_arns and envelope.get("TopicArn") not in self._topic_arns:
            raise WebhookVerificationError("unexpected SNS topic")

        message_type = envelope.get("Type")
        if message_type == "SubscriptionConfirmation":
            subscribe_url = envelope.get("SubscribeURL", "")
            host = urlparse(subscribe_url).hostname or ""
            if not host.endswith(".amazonaws.com"):
                raise WebhookVerificationError("untrusted SubscribeURL")
            httpx.get(subscribe_url, timeout=15)
            return []
        if message_type != "Notification":
            return []

        notification = json.loads(envelope["Message"])
        if notification.get("notificationType") != "Received":
            return []
        mail = notification["mail"]
        receipt = notification["receipt"]
        raw = self._fetch_raw(notification, receipt)
        parsed = message_from_bytes(raw, policy=policy.default)

        text = html = None
        body = parsed.get_body(preferencelist=("plain",))
        if body is not None:
            text = body.get_content()
        body = parsed.get_body(preferencelist=("html",))
        if body is not None:
            html = body.get_content()

        sender_name, sender_address = parseaddr(str(parsed.get("From", "")))
        recipients = [
            {"address": address, "name": name or None}
            for name, address in getaddresses([str(parsed.get("To", ""))])
            if address
        ]
        message_id = (parsed.get("Message-ID") or f"<{mail['messageId']}@ses>").strip()
        references = str(parsed.get("References", "")).split()
        in_reply_to = str(parsed.get("In-Reply-To", "")).strip()
        thread_root = references[0] if references else (in_reply_to or message_id)

        auto_generated = _is_auto_generated(parsed, sender_address or None)
        inbound = []
        targets = receipt.get("recipients") or [r["address"] for r in recipients]
        for index, target in enumerate(targets):
            suffix = f":{index}" if len(targets) > 1 else ""
            inbound.append(
                InboundMessage(
                    external_event_id=f"{mail['messageId']}{suffix}",
                    provider_inbox_id=target.lower(),
                    provider_message_id=message_id,
                    provider_thread_id=thread_root,
                    sender_address=sender_address or None,
                    sender_name=sender_name or None,
                    recipients=recipients,
                    subject=str(parsed.get("Subject", "")) or None,
                    text=text,
                    html=html,
                    auto_generated=auto_generated,
                )
            )
        return inbound

    def _fetch_raw(self, notification: dict, receipt: dict) -> bytes:
        action = receipt.get("action", {})
        if action.get("type") == "S3":
            bucket = action.get("bucketName") or self._bucket
            key = action["objectKey"]
            return self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        content = notification.get("content")
        if content:
            try:
                return base64.b64decode(content, validate=True)
            except Exception:
                return content.encode()
        raise WebhookVerificationError("notification carries no message content")
