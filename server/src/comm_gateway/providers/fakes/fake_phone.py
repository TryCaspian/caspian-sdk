"""In-memory phone (SMS) provider for local development and tests.

Consumes real Telnyx `message.received` webhook shapes so tests exercise the
same normalization path as the live adapter. Simulates per-agent number
commissioning: each provision() hands out a fresh number, and release() gives it
back — so the provisioning lifecycle is covered without a paid carrier account.
"""

import secrets

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from ..phone import TelnyxPhoneProvider, parse_event


class FakePhoneProvider:
    name = "fake-phone"
    channel = "phone"
    capabilities = TelnyxPhoneProvider.capabilities

    def __init__(self, from_number: str = "") -> None:
        # Shared-number mode when from_number is set; otherwise commission one per
        # connection (like the real provider's search+buy path).
        self._fixed_from = from_number
        self._counter = 0
        self.sent: list[dict] = []
        self.provisioned: list[str] = []
        self.released: list[str] = []

    def _record(self, from_number: str, to_number: str, text: str | None) -> SendResult:
        self.sent.append({"from": from_number, "to": to_number, "text": text})
        return SendResult(
            provider_message_id=f"{to_number}:{secrets.token_hex(6)}",
            provider_thread_id=to_number,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        if self._fixed_from:
            return ProvisionResult(
                address=self._fixed_from, provider_resource_id=self._fixed_from
            )
        self._counter += 1
        number = f"+1555000{self._counter:04d}"
        self.provisioned.append(number)
        return ProvisionResult(
            address=number, provider_resource_id=number, provider_pod_id=f"PN{self._counter}"
        )

    def release(self, provider_resource_id: str, provider_pod_id: str | None) -> None:
        self.released.append(provider_resource_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._record(provider_inbox_id, message.to[0], message.text)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _, _ = provider_message_id.partition(":")
        return self._record(provider_inbox_id, remote_number, message.text)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._record(provider_inbox_id, recipient, message.text)

    def parse_webhook(self, payload: bytes, headers, credentials=None) -> list[InboundMessage]:
        import json  # noqa: PLC0415

        return parse_event(json.loads(payload), self._fixed_from or "")

    def webhook_payload(
        self,
        *,
        to_number: str,
        from_number: str = "+15551234567",
        text: str = "Hi",
        event_id: str | None = None,
        message_id: str | None = None,
        carrier: str = "T-Mobile USA",
    ) -> dict:
        return {
            "data": {
                "event_type": "message.received",
                "id": event_id or f"evt_{secrets.token_hex(6)}",
                "payload": {
                    "direction": "inbound",
                    "id": message_id or f"msg_{secrets.token_hex(6)}",
                    "type": "SMS",
                    "from": {"phone_number": from_number, "carrier": carrier},
                    "to": [{"phone_number": to_number, "status": "webhook_delivered"}],
                    "text": text,
                    "media": [],
                },
            }
        }
