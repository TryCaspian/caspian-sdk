"""Managed phone adapter - SIM-backed real-mobile numbers via a provider API.

The upstream provisions real carrier lines (not VoIP), which is why they pass
Meta/Google/OTP line-type checks where Twilio/Telnyx numbers don't. We use it
primarily to COMMISSION real numbers; it is an optional, removable provider kept
behind the same ChannelProvider seam so it can be swapped out.

The upstream base URL and API key are configuration (COMM_CASPIAN_PHONE_BASE_URL,
COMM_CASPIAN_PHONE_API_KEY), never hardcoded - bring your own compatible endpoint.
Endpoints: GET/POST /v1/numbers to list/order, POST /v1/messages to send, and
HMAC-signed inbound webhooks. The fake provider covers the gateway wiring end to
end so this can be exercised offline.
"""

import hashlib
import hmac
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

SIGNATURE_HEADER = "x-webhook-signature"


def parse_message_event(payload: dict) -> list[InboundMessage]:
    """Normalize a managed-phone inbound webhook into our schema.

    Shape: top-level `event`, `channel`, `timestamp`, `agentId`, and a nested
    `data` object with `from`/`to`/`message`/`direction`/`numberId`/
    `conversationId`/`receivedAt`. No message id is sent, so the inbound is keyed
    on numberId+receivedAt+from.
    """
    if payload.get("event") != "agent.message":
        return []
    d = payload["data"]
    if d.get("direction") != "inbound":
        return []
    remote = d["from"]
    stamp = d.get("receivedAt") or payload.get("timestamp", "")
    return [
        InboundMessage(
            external_event_id=f"{d.get('numberId', '')}:{stamp}:{remote}",
            provider_inbox_id=d["to"],
            provider_message_id=f"{remote}:{stamp}",
            provider_thread_id=remote,
            sender_address=remote,
            recipients=[{"address": d["to"]}],
            text=d.get("message"),
            chat_type=payload.get("channel", "sms"),
        )
    ]


class CaspianPhoneProvider:
    name = "caspian-phone"
    channel = "phone"
    # SIM-backed real lines: reliable OTP (the reason to use it over Twilio).
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
            Capability.OTP,
        }
    )

    def __init__(
        self,
        api_key: str,
        webhook_secret: str = "",
        base_url: str = "",
    ) -> None:
        if not api_key:
            raise ValueError("COMM_CASPIAN_PHONE_API_KEY is required for the caspian-phone provider")
        if not base_url:
            raise ValueError(
                "COMM_CASPIAN_PHONE_BASE_URL is required for the caspian-phone provider"
            )
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    def _send(self, from_number: str, to_number: str, text: str) -> SendResult:
        r = self._client.post(
            "/v1/messages",
            json={"body": text, "to_number": to_number, "from_number": from_number},
        )
        r.raise_for_status()
        data = r.json()
        return SendResult(
            provider_message_id=f"{to_number}:{data['id']}", provider_thread_id=to_number
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # Reuse the account's active number (single-line accounts). Ordering a new
        # number per agent is a paid POST /v1/numbers - a follow-up.
        r = self._client.get("/v1/numbers")
        r.raise_for_status()
        numbers = r.json().get("data", [])
        active = next((n for n in numbers if n.get("status") == "active"), None)
        if active is None:
            raise RuntimeError("no active caspian-phone number on this account")
        return ProvisionResult(
            address=active["phoneNumber"],
            provider_resource_id=active["phoneNumber"],
            provider_pod_id=active["id"],
        )

    def poll_inbound(self, number_id: str) -> list[InboundMessage]:
        """Pull inbound SMS for a number (the upstream is webhook-native; this is a
        poll fallback used to read OTPs without a public webhook)."""
        r = self._client.get(f"/v1/numbers/{number_id}/messages")
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            if m.get("direction") != "inbound":
                continue
            remote = m["from_"]
            out.append(
                InboundMessage(
                    external_event_id=m["id"],
                    provider_inbox_id=m["to"],
                    provider_message_id=f"{remote}:{m['id']}",
                    provider_thread_id=remote,
                    sender_address=remote,
                    recipients=[{"address": m["to"]}],
                    text=m.get("body"),
                    chat_type=m.get("channel", "sms"),
                )
            )
        return out

    def release(self, provider_resource_id: str, provider_pod_id: str | None) -> None:
        if provider_pod_id:
            self._client.delete(f"/v1/numbers/{provider_pod_id}")

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send(provider_inbox_id, message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _ = split_composite_id(provider_message_id)
        return self._send(provider_inbox_id, remote_number, message.text or "")

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._send(provider_inbox_id, recipient, message.text or "")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        data = json.loads(payload)
        if self._webhook_secret:
            received = lower_headers(headers).get(SIGNATURE_HEADER, "")
            signed = f"{data.get('timestamp', '')}.".encode() + payload
            expected = "sha256=" + hmac.new(
                self._webhook_secret.encode(), signed, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(received, expected):
                raise WebhookVerificationError("caspian-phone signature mismatch")
        return parse_message_event(data)
