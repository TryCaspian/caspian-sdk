"""In-memory fakes for the WhatsApp, RCS, and iMessage channels (tests)."""

import secrets
from urllib.parse import urlencode

from ..base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from ..caspian_phone_imessage import CaspianPhoneIMessageProvider
from ..twilio_rcs import parse_rcs_webhook
from ..twilio_whatsapp import parse_whatsapp_webhook
from .fake_caspian_phone import FakeCaspianPhoneProvider

_TWILIO_CHANNEL_CAPS = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})


class _FakeTwilioChannel:
    """Shared fake for Twilio-backed channels (WhatsApp, RCS) — form webhooks."""

    channel = "override"
    name = "override"
    capabilities = _TWILIO_CHANNEL_CAPS
    _parse = staticmethod(parse_whatsapp_webhook)

    def __init__(self, from_number: str = "+15550000000") -> None:
        self._from = from_number
        self.sent: list[dict] = []

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(address=self._from, provider_resource_id=self._from)

    def _record(self, to_number: str, text: str | None) -> SendResult:
        self.sent.append({"from": self._from, "to": to_number, "text": text})
        return SendResult(
            provider_message_id=f"{to_number}:{secrets.token_hex(5)}", provider_thread_id=to_number
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._record(message.to[0], message.text)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote, _, _ = provider_message_id.partition(":")
        return self._record(remote, message.text)

    def parse_webhook(self, payload: bytes, headers, credentials=None) -> list[InboundMessage]:
        return self._parse(payload, self._from)

    def webhook_payload(self, *, from_number: str = "+15551112222", text: str = "Hi") -> str:
        # Twilio form-encoded inbound (urlencode so "+" in numbers survives).
        return urlencode(
            {
                "MessageSid": f"SM{secrets.token_hex(6)}",
                "From": from_number,
                "To": self._from,
                "Body": text,
            }
        )


class FakeWhatsAppProvider(_FakeTwilioChannel):
    name = "fake-whatsapp"
    channel = "whatsapp"
    _parse = staticmethod(parse_whatsapp_webhook)


class FakeRcsProvider(_FakeTwilioChannel):
    name = "fake-rcs"
    channel = "rcs"
    _parse = staticmethod(parse_rcs_webhook)


class FakeCaspianPhoneIMessageProvider(FakeCaspianPhoneProvider):
    name = "fake-caspian-imessage"
    channel = "imessage"
    capabilities = CaspianPhoneIMessageProvider.capabilities


