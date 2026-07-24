"""In-memory real-mobile-line provider for tests (stands in for a USB modem).

Lets the whole OTP path — inbound SMS -> normalized message -> extracted code
-> message.otp event — be exercised without any hardware. Its webhook_payload
mimics an SMS the modem's poll loop would inject.
"""

import json
import secrets

from .base import (
    InboundEvent,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from .modem import GsmModemProvider


class FakeModemProvider:
    name = "fake-modem"
    channel = "phone"
    capabilities = GsmModemProvider.capabilities

    def __init__(self, msisdn: str = "+15557654321") -> None:
        self.msisdn = msisdn
        self.sent: list[dict] = []

    def _record(self, to_number: str, text: str | None) -> SendResult:
        self.sent.append({"from": self.msisdn, "to": to_number, "text": text})
        return SendResult(
            provider_message_id=f"{to_number}:{secrets.token_hex(4)}",
            provider_thread_id=to_number,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(address=self.msisdn, provider_resource_id=self.msisdn)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._record(message.to[0], message.text)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _, _ = provider_message_id.partition(":")
        return self._record(remote_number, message.text)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._record(recipient, message.text)

    def parse_webhook(self, payload: bytes, headers, credentials=None) -> list[InboundEvent]:
        data = json.loads(payload)
        return [
            InboundMessage(
                external_event_id=data["id"],
                provider_inbox_id=self.msisdn,
                provider_message_id=f"{data['from']}:{data['id']}",
                provider_thread_id=data["from"],
                sender_address=data["from"],
                recipients=[{"address": self.msisdn}],
                text=data["text"],
                chat_type="sms",
            )
        ]

    def webhook_payload(
        self, *, from_number: str = "+15551112222", text: str = "Hi", event_id: str | None = None
    ) -> dict:
        return {
            "id": event_id or f"sms_{secrets.token_hex(5)}",
            "from": from_number,
            "text": text,
        }
