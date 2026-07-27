"""WhatsApp adapter on Twilio (official WhatsApp Business Solution Provider).

Same Twilio Messages API as SMS, but From/To are `whatsapp:` addresses. Business
messaging only (Meta's sanctioned path): free-form replies work inside the 24h
customer-service window; cold-starting a conversation needs a pre-approved
template, so INITIATE is intentionally not offered here.
"""

from collections.abc import Mapping
from urllib.parse import parse_qs

import httpx

from ._twilio_sig import verify_twilio_webhook
from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    split_composite_id,
)


def _strip(addr: str) -> str:
    return addr.removeprefix("whatsapp:")


def parse_whatsapp_webhook(payload: bytes, sender: str) -> list[InboundMessage]:
    form = {k: v[0] for k, v in parse_qs(payload.decode()).items()}
    if not form.get("MessageSid") or form.get("Body") is None:
        return []
    remote = _strip(form["From"])
    return [
        InboundMessage(
            external_event_id=form["MessageSid"],
            provider_inbox_id=_strip(form.get("To", sender)),
            provider_message_id=f"{remote}:{form['MessageSid']}",
            provider_thread_id=remote,
            sender_address=remote,
            recipients=[{"address": _strip(form.get("To", sender))}],
            text=form["Body"],
            chat_type="whatsapp",
        )
    ]


class TwilioWhatsAppProvider:
    name = "twilio-whatsapp"
    channel = "whatsapp"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str = "",
        pool: str = "",
        base_url: str = "https://api.twilio.com",
        verify_url: str = "",
    ) -> None:
        # Pool = Caspian-owned Twilio WhatsApp senders handed out one-per-agent
        # (Option 1a). from_number is the single shared default when there's no
        # pool. Exactly one of the two must be configured.
        self._pool = [n.strip() for n in pool.split(",") if n.strip()]
        if not (account_sid and auth_token and (from_number or self._pool)):
            raise ValueError(
                "COMM_TWILIO_ACCOUNT_SID, COMM_TWILIO_AUTH_TOKEN and one of "
                "COMM_TWILIO_WHATSAPP_FROM / COMM_TWILIO_WHATSAPP_POOL are required "
                "for the twilio-whatsapp provider"
            )
        self._sid = account_sid
        self._auth_token = auth_token
        self._verify_url = verify_url
        # Shared default sender; may be empty in a pool-only deployment.
        self._from = from_number
        self._client = httpx.Client(
            base_url=base_url, auth=(account_sid, auth_token), timeout=30.0
        )

    @property
    def pool_numbers(self) -> list[str]:
        """Caspian-owned Twilio WhatsApp senders available for per-agent handout."""
        return list(self._pool)

    def _sender_for(self, credentials) -> str:
        """The From this connection sends as: its assigned pool number, else the
        shared default."""
        return (credentials or {}).get("from_number") or self._from

    def _send(self, from_number: str, to_number: str, text: str) -> SendResult:
        r = self._client.post(
            f"/2010-04-01/Accounts/{self._sid}/Messages.json",
            data={
                "From": f"whatsapp:{_strip(from_number)}",
                "To": f"whatsapp:{_strip(to_number)}",
                "Body": text,
            },
        )
        r.raise_for_status()
        return SendResult(
            provider_message_id=f"{_strip(to_number)}:{r.json()['sid']}",
            provider_thread_id=_strip(to_number),
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # The number pinned at connect time (a pool sender) rides in credentials;
        # with no pool it's the shared default. Address == resource_id so inbound
        # routes to this connection by the number it received on.
        number = self._sender_for(request.credentials)
        return ProvisionResult(address=number, provider_resource_id=number)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send(self._sender_for(credentials), message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _ = split_composite_id(provider_message_id)
        return self._send(self._sender_for(credentials), remote_number, message.text or "")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        verify_twilio_webhook(self._auth_token, self._verify_url, payload, headers)
        # Per-number: Twilio's inbound form carries the exact To (the sender the
        # customer messaged), so the inbox resolves to that number; the fallback
        # only matters if To is somehow absent.
        return parse_whatsapp_webhook(payload, self._sender_for(credentials))
