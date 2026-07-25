"""In-memory Linear provider for gateway tests."""

import json
import secrets

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from ..linear import (
    LinearProvider,
    issue_id_from_provider_message_id,
    parse_linear_webhook,
)


class FakeLinearProvider:
    name = "fake-linear"
    channel = "linear"
    capabilities = LinearProvider.capabilities
    connect_credentials = ()

    route_key = staticmethod(LinearProvider.route_key)

    def __init__(self) -> None:
        self.organization_id = f"org_{secrets.token_hex(4)}"
        self.team_id = f"team_{secrets.token_hex(4)}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"linear:{self.organization_id}",
            provider_resource_id=self.organization_id,
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        team_id = message.to[0] if message.to else self.team_id
        self._seq += 1
        issue_id = f"issue_{self._seq}"
        self.sent.append({"team_id": team_id, "subject": message.subject, "text": message.text})
        return SendResult(provider_message_id=issue_id, provider_thread_id=issue_id)

    def reply(
        self,
        provider_inbox_id,
        provider_message_id,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        issue_id = issue_id_from_provider_message_id(provider_message_id)
        self._seq += 1
        comment_id = f"comment_{self._seq}"
        self.replies.append({"issue_id": issue_id, "text": message.text})
        return SendResult(
            provider_message_id=f"{issue_id}:{comment_id}",
            provider_thread_id=issue_id,
        )

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_linear_webhook(data, headers.get("Linear-Delivery"))

    def issue_payload(self, *, title="Bug in checkout", description="Payment fails") -> dict:
        self._seq += 1
        return {
            "action": "create",
            "type": "Issue",
            "organizationId": self.organization_id,
            "webhookTimestamp": 1_752_000_000_000 + self._seq,
            "data": {
                "id": f"issue_{self._seq}",
                "identifier": f"LIN-{self._seq}",
                "title": title,
                "description": description,
                "creator": {
                    "id": "user_1",
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                },
            },
        }
    def comment_payload(self, *, issue_id="issue_1", body="Any update?") -> dict:
        self._seq += 1
        return {
            "action": "create",
            "type": "Comment",
            "organizationId": self.organization_id,
            "webhookTimestamp": 1_752_000_000_000 + self._seq,
            "data": {
                "id": f"comment_{self._seq}",
                "body": body,
                "issue": {"id": issue_id, "identifier": "LIN-1", "title": "Bug"},
                "user": {"id": "user_2", "name": "Grace Hopper"},
            },
        }
