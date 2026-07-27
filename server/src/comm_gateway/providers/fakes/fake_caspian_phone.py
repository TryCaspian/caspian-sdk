"""In-memory managed-phone provider for tests - simulates SIM-backed provisioning."""

import secrets

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from ..caspian_phone import CaspianPhoneProvider, parse_message_event


class FakeCaspianPhoneProvider:
    name = "fake-caspian-phone"
    channel = "phone"
    capabilities = CaspianPhoneProvider.capabilities

    def __init__(self) -> None:
        self._counter = 0
        self.sent: list[dict] = []
        self.provisioned: list[str] = []
        self.released: list[str] = []

    def _send(self, from_number: str, to_number: str, text: str | None) -> SendResult:
        self.sent.append({"from": from_number, "to": to_number, "text": text})
        return SendResult(
            provider_message_id=f"{to_number}:{secrets.token_hex(5)}", provider_thread_id=to_number
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        self._counter += 1
        number = f"+1917000{self._counter:04d}"  # "real mobile" look
        self.provisioned.append(number)
        return ProvisionResult(
            address=number, provider_resource_id=number, provider_pod_id=f"ap_{self._counter}"
        )

    def release(self, provider_resource_id: str, provider_pod_id: str | None) -> None:
        self.released.append(provider_resource_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send(provider_inbox_id, message.to[0], message.text)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _, _ = provider_message_id.partition(":")
        return self._send(provider_inbox_id, remote_number, message.text)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._send(provider_inbox_id, recipient, message.text)

    def parse_webhook(self, payload: bytes, headers, credentials=None) -> list[InboundMessage]:
        import json  # noqa: PLC0415

        return parse_message_event(json.loads(payload))

    def webhook_payload(
        self, *, to_number: str, from_number: str = "+15551112222", text: str = "Hi"
    ) -> dict:
        # Matches the managed-phone inbound `agent.message` shape.
        return {
            "event": "agent.message",
            "channel": "sms",
            "timestamp": "2026-07-13T11:00:00Z",
            "agentId": f"agt_{secrets.token_hex(4)}",
            "data": {
                "conversationId": f"conv_{secrets.token_hex(4)}",
                "numberId": f"num_{secrets.token_hex(4)}",
                "from": from_number,
                "to": to_number,
                "message": text,
                "direction": "inbound",
                "receivedAt": f"2026-07-13T11:00:0{secrets.randbelow(9)}Z",
            },
        }
