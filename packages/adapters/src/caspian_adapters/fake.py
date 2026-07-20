"""In-memory email provider for local development and tests."""

import json
import re
import secrets
from collections.abc import Mapping

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "agent"


class FakeEmailProvider:
    name = "fake"
    channel = "email"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY})

    def __init__(self) -> None:
        self.inboxes: dict[str, dict] = {}
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self.domains: list[str] = []
        self.inbound_domains: list[str] = []

    def create_domain(self, domain: str) -> list[dict]:
        self.domains.append(domain)
        token = secrets.token_hex(16)
        return [
            {"type": "CNAME", "name": f"{token}._domainkey.{domain}",
             "value": f"{token}.dkim.fake.comm.local"},
            {"type": "MX", "name": domain, "value": "inbound.fake.comm.local", "priority": 10},
        ]

    def check_domain(self, domain: str) -> bool:
        return domain in self.domains

    def enable_inbound(self, domain: str) -> None:
        self.inbound_domains.append(domain)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        inbox_id = f"inbox_{secrets.token_hex(6)}"
        local_part = request.username or (
            f"{_slug(request.display_name or request.agent_id)}-{secrets.token_hex(3)}"
        )
        address = f"{local_part}@{request.domain or 'sandbox.comm.local'}"
        self.inboxes[inbox_id] = {"address": address, "connection_id": request.connection_id}
        return ProvisionResult(
            address=address,
            provider_resource_id=inbox_id,
            provider_pod_id=f"pod_{request.customer_id}",
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        record = {
            "inbox_id": provider_inbox_id,
            "to": list(message.to),
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
        }
        self.sent.append(record)
        return SendResult(
            provider_message_id=f"fake_msg_{secrets.token_hex(6)}",
            provider_thread_id=f"fake_thread_{secrets.token_hex(6)}",
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        record = {
            "inbox_id": provider_inbox_id,
            "in_reply_to": provider_message_id,
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
        }
        self.replies.append(record)
        return SendResult(provider_message_id=f"fake_msg_{secrets.token_hex(6)}")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        if data.get("type") != "message.received":
            return []
        message = data["message"]
        return [
            InboundMessage(
                external_event_id=data["event_id"],
                provider_inbox_id=message["inbox_id"],
                provider_message_id=message["message_id"],
                provider_thread_id=message["thread_id"],
                sender_address=message.get("from", {}).get("address"),
                sender_name=message.get("from", {}).get("name"),
                recipients=message.get("to", []),
                subject=message.get("subject"),
                text=message.get("text"),
                html=message.get("html"),
                auto_generated=bool(message.get("auto_generated")),
            )
        ]

    def send_test_email(
        self, provider_inbox_id: str, to_address: str, subject: str, text: str
    ) -> InboundMessage:
        return InboundMessage(
            external_event_id=f"test_evt_{secrets.token_hex(6)}",
            provider_inbox_id=provider_inbox_id,
            provider_message_id=f"test_msg_{secrets.token_hex(6)}",
            provider_thread_id=f"test_thread_{secrets.token_hex(6)}",
            sender_address="tester@sandbox.comm.local",
            sender_name="Comm Tester",
            recipients=[{"address": to_address}],
            subject=subject,
            text=text,
        )

    @staticmethod
    def webhook_payload(
        inbox_id: str,
        *,
        sender: str = "customer@example.com",
        sender_name: str | None = "Customer",
        subject: str = "Hello",
        text: str = "Hi there",
        event_id: str | None = None,
        thread_id: str | None = None,
        message_id: str | None = None,
    ) -> dict:
        return {
            "event_id": event_id or f"fake_evt_{secrets.token_hex(6)}",
            "type": "message.received",
            "message": {
                "inbox_id": inbox_id,
                "message_id": message_id or f"fake_msg_{secrets.token_hex(6)}",
                "thread_id": thread_id or f"fake_thread_{secrets.token_hex(6)}",
                "from": {"address": sender, "name": sender_name},
                "to": [],
                "subject": subject,
                "text": text,
            },
        }
