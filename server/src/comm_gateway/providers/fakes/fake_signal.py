"""In-memory Signal provider for tests - no signal-cli daemon required."""

import json
import secrets

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from ..signal import SignalProvider, parse_envelope, target_params


class FakeSignalProvider:
    name = "fake-signal"
    channel = "signal"
    connect_credentials: tuple[str, ...] = ()
    capabilities = SignalProvider.capabilities

    def __init__(self, number: str = "+15550001111") -> None:
        self._number = number
        self.sent: list[dict] = []

    def _send(self, target: str, text: str | None) -> SendResult:
        # Record the addressing the real provider would use, so a fake-backed
        # test still catches a DM/group mix-up.
        self.sent.append({"target": target, "text": text, **target_params(target)})
        return SendResult(
            provider_message_id=f"{target}:{secrets.token_hex(5)}", provider_thread_id=target
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(address=self._number, provider_resource_id=self._number)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send(message.to[0], message.text)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        target, _, _ = provider_message_id.partition(":")
        return self._send(target, message.text)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._send(recipient, message.text)

    def parse_webhook(self, payload: bytes, headers, credentials=None) -> list[InboundMessage]:
        # Reuse the real parser: the fake must consume the same signal-cli
        # payload shape production does, or it proves nothing.
        return parse_envelope(json.loads(payload), self._number)
