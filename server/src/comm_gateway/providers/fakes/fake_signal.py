"""In-memory Signal provider for local development and tests.

Consumes real Signal envelope shapes so tests exercise the same
normalization path as the live adapter.
"""

import hmac
import json
from collections.abc import Mapping

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)
from .signal import SECRET_HEADER, SignalProvider, parse_envelope


class FakeSignalProvider:
    name = "fake-signal"
    channel = "signal"
    capabilities = SignalProvider.capabilities
    connect_credentials = ()
    optional_connect_credentials = ("number", "webhook_secret")

    def __init__(self, number: str = "+15559876543", webhook_secret: str = "") -> None:
        self.number = number
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._webhook_secret = webhook_secret
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        num = (request.credentials or {}).get("number") or self.number
        return ProvisionResult(address=num, provider_resource_id=num)

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        target = message.to[0] if message.to else ""
        self.sent.append({"to": target, "text": message.text})
        self._seq += 1
        ts = str(1_752_400_000_000 + self._seq)
        return SendResult(provider_message_id=f"{target}:{ts}", provider_thread_id=target)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        self.replies.append({"in_reply_to": provider_message_id, "text": message.text})
        self._seq += 1
        ts = str(1_752_400_000_000 + self._seq)
        head = provider_message_id.rpartition(":")[0]
        return SendResult(provider_message_id=f"{head}:{ts}", provider_thread_id=head)

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("webhook_secret") or self._webhook_secret
        if secret:
            received = lower_headers(headers).get(SECRET_HEADER) or ""
            if not hmac.compare_digest(received, secret):
                raise WebhookVerificationError("secret token mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_envelope(data, local_number=self.number)

    def webhook_payload(
        self,
        *,
        sender: str = "+15551112222",
        text: str = "Hi there",
        timestamp: int | None = None,
        group_id: str | None = None,
    ) -> dict:
        self._seq += 1
        ts = timestamp or (1_752_400_000_000 + self._seq)
        data_msg: dict = {"timestamp": ts, "message": text}
        if group_id:
            data_msg["groupInfo"] = {"groupId": group_id, "type": "DELIVER"}
        return {
            "envelope": {
                "source": sender,
                "sourceNumber": sender,
                "sourceName": "Alice",
                "timestamp": ts,
                "dataMessage": data_msg,
            },
            "account": self.number,
        }
