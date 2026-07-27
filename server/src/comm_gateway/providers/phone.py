"""Phone (SMS/MMS) adapter on Telnyx.

Maps our provider contract onto one Telnyx sender number per deployment:
- the connection address is the agent's E.164 number
- provider_thread_id is the remote party's E.164 number (one conversation per
  remote number)
- provider_message_id is "{remote_number}:{telnyx_id}" so a reply can route to
  the remote party without a lookup (the composite id never leaves this package)
- inbound arrives as Telnyx `message.received` webhooks, optionally verified with
  the Ed25519 public key from the Telnyx portal

Compliance note (see README): US application-to-person SMS on long codes requires
A2P 10DLC brand + campaign registration (~7-15 day review, 1 msg/sec on a starter
campaign). That gate lives at the Telnyx account/messaging-profile level, not in
this adapter; provisioning a live 10DLC number is an onboarding step, and a
toll-free number is the same-day fast path.
"""

import json
from collections.abc import Mapping

import httpx

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
    split_composite_id,
)


def parse_event(data: dict, agent_number: str = "") -> list[InboundMessage]:
    """Normalize a Telnyx messaging webhook into our schema (inbound SMS/MMS only).

    The agent's number is taken from the webhook's `to` (so per-agent commissioned
    numbers route to the right connection), falling back to agent_number.
    Delivery-status events (message.sent / message.finalized) are ignored here.
    """
    if data.get("data", {}).get("event_type") != "message.received":
        return []
    payload = data["data"]["payload"]
    remote = payload["from"]["phone_number"]
    to_list = payload.get("to") or []
    inbox = to_list[0]["phone_number"] if to_list else agent_number
    # MMS media handling is a follow-up; text SMS only for now.
    return [
        InboundMessage(
            external_event_id=data["data"]["id"],
            provider_inbox_id=inbox,
            provider_message_id=f"{remote}:{payload['id']}",
            provider_thread_id=remote,
            sender_address=remote,
            sender_name=payload["from"].get("carrier"),
            recipients=[{"address": inbox}],
            text=payload.get("text"),
            html=None,
            chat_type="sms",
        )
    ]


class TelnyxPhoneProvider:
    name = "telnyx"
    channel = "phone"
    # SMS can cold-start (INITIATE) — unlike a Telegram bot, no prior inbound is
    # required to message a number. OTP is BEST-EFFORT on a CPaaS line: services
    # that don't line-type-filter (a large long tail) deliver codes fine; the
    # strict verifiers (Google/WhatsApp/banks) reject VoIP numbers. For reliable
    # OTP everywhere, use the gsm-modem (real-SIM) provider.
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
            Capability.OTP,
        }
    )
    # BYO: the developer brings their own Telnyx API key + number.
    connect_credentials: tuple[str, ...] = ("api_key", "from_number")

    def __init__(
        self,
        api_key: str = "",
        from_number: str = "",
        messaging_profile_id: str = "",
        public_key: str = "",
        base_url: str = "https://api.telnyx.com",
    ) -> None:
        # Deployment fallback (optional). BYO connections carry their own key +
        # number, so the provider is constructable with none of these set.
        self._api_key = api_key
        self._from_number = from_number
        self._messaging_profile_id = messaging_profile_id
        self._public_key = public_key
        # Auth is supplied PER REQUEST from the connection's creds (BYO).
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def _creds(self, credentials) -> tuple[str, str, str, str]:
        """(api_key, from_number, messaging_profile_id, public_key) for this
        connection, falling back to the deployment account per field."""
        c = credentials or {}
        return (
            c.get("api_key") or self._api_key,
            c.get("from_number") or self._from_number,
            c.get("messaging_profile_id") or self._messaging_profile_id,
            c.get("public_key") or self._public_key,
        )

    def _send_sms(self, credentials, to_number: str, message: OutboundMessage) -> SendResult:
        api_key, from_number, profile, _ = self._creds(credentials)
        body: dict = {"from": from_number, "to": to_number, "text": message.text or ""}
        if profile:
            body["messaging_profile_id"] = profile
        response = self._client.post(
            "/v2/messages", json=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        data = response.json()["data"]
        return SendResult(
            provider_message_id=f"{to_number}:{data['id']}",
            provider_thread_id=to_number,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # BYO: the number IS the connection's from_number (no commissioning).
        _, from_number, _, _ = self._creds(request.credentials)
        if not from_number:
            raise ValueError(
                "telnyx needs a from_number (bring your own Telnyx number at connect)"
            )
        return ProvisionResult(address=from_number, provider_resource_id=from_number)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send_sms(credentials, message.to[0], message)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _ = split_composite_id(provider_message_id)
        return self._send_sms(credentials, remote_number, message)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._send_sms(credentials, recipient, message)

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        _, from_number, _, public_key = self._creds(credentials)
        if public_key:
            self._verify_signature(public_key, payload, headers)
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_event(data, from_number)

    def _verify_signature(
        self, public_key: str, payload: bytes, headers: Mapping[str, str]
    ) -> None:
        # Telnyx signs webhooks with Ed25519 over "{timestamp}|{body}".
        import base64  # noqa: PLC0415

        from nacl.exceptions import BadSignatureError  # noqa: PLC0415
        from nacl.signing import VerifyKey  # noqa: PLC0415

        lower = lower_headers(headers)
        signature = lower.get("telnyx-signature-ed25519")
        timestamp = lower.get("telnyx-timestamp")
        if not signature or not timestamp:
            raise WebhookVerificationError("missing Telnyx signature headers")
        signed = f"{timestamp}|".encode() + payload
        try:
            VerifyKey(base64.b64decode(public_key)).verify(
                signed, base64.b64decode(signature)
            )
        except (BadSignatureError, ValueError) as exc:
            raise WebhookVerificationError("Telnyx signature mismatch") from exc
