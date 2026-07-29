"""In-memory Linear provider using real comment payload shapes."""

import json
import secrets

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
    split_composite_id,
)
from ..linear import LinearProvider, parse_linear_comment, verify_linear_timestamp


class FakeLinearProvider:
    name = "fake-linear"
    channel = "linear"
    capabilities = LinearProvider.capabilities
    connect_credentials = ("organization_id",)
    optional_connect_credentials = ("api_key", "webhook_secret")
    client_id = "fake_linear_client_123"

    def __init__(self) -> None:
        self.organization_id = f"org_fake_{secrets.randbelow(1_000_000)}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 1000

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        return LinearProvider.route_key(payload)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = request.credentials or {}
        if credentials.get("organization_id"):
            self.organization_id = credentials["organization_id"]
        return ProvisionResult(
            address=credentials.get("address", "linear:acme"),
            provider_resource_id=credentials.get("organization_id", self.organization_id),
        )

    def _result(self, thread_id: str, target: list[dict], text: str | None) -> SendResult:
        self._seq += 1
        target.append({"thread_id": thread_id, "text": text})
        return SendResult(
            provider_message_id=f"{thread_id}:{self._seq}",
            provider_thread_id=thread_id,
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        if not message.to:
            raise ValueError("Linear send requires target issue in message.to")
        return self._result(message.to[0].strip(), self.sent, message.text)

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        thread_id, _ = split_composite_id(provider_message_id)
        return self._result(thread_id, self.replies, message.text)

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        verify_linear_timestamp(data)
        header_map = lower_headers(headers)
        return parse_linear_comment(
            data,
            delivery_id=header_map.get("linear-delivery", header_map.get("x-delivery", "")),
        )

    def webhook_payload(
        self,
        *,
        issue_id: str = "ENG-42",
        comment_id: str | None = None,
        user: str = "jane@example.com",
        text: str = "Fixing issue via PR",
        action: str = "create",
        event_type: str = "Comment",
        actor_type: str = "user",
    ) -> dict:
        self._seq += 1
        cid = comment_id or f"comment_{self._seq}"
        return {
            "action": action,
            "type": event_type,
            "organizationId": self.organization_id,
            "actor": {
                "id": f"usr_{self._seq}",
                "name": "Test User",
                "email": user,
                "type": actor_type,
            },
            "data": {
                "id": cid,
                "body": text,
                "issue": {"id": f"issue_uuid_{self._seq}", "identifier": issue_id},
                "user": {"id": f"usr_{self._seq}", "name": "Test User", "email": user},
            },
        }
