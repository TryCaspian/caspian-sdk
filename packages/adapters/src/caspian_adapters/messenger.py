"""Instagram DM + Facebook Messenger adapters (Meta Graph API).

Graph API messaging: send via POST /{id}/messages, inbound
via a signed webhook (X-Hub-Signature-256, HMAC of the raw body with the app
secret) plus a GET hub-challenge for subscription. Instagram and Messenger
share the messaging envelope, so both subclass one base and differ only in the
channel name and the send product tag.

Like WhatsApp business messaging, free-form replies are allowed inside the
platform's messaging window; cold-start requires message tags/templates, so
INITIATE is not offered here.
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


def parse_messaging_webhook(payload: bytes, page_id: str, channel: str) -> list[InboundMessage]:
    data = json.loads(payload)
    out: list[InboundMessage] = []
    for entry in data.get("entry", []):
        recipient_id = entry.get("id", page_id)
        for m in entry.get("messaging", []):
            message = m.get("message")
            if not message or message.get("is_echo") or not message.get("text"):
                continue
            sender = m["sender"]["id"]
            mid = message.get("mid", "")
            out.append(
                InboundMessage(
                    external_event_id=mid,
                    provider_inbox_id=recipient_id,
                    provider_message_id=f"{sender}:{mid}",
                    provider_thread_id=sender,
                    sender_address=sender,
                    recipients=[{"address": recipient_id}],
                    text=message["text"],
                    chat_type=channel,
                )
            )
    return out


class _MetaMessagingProvider:
    channel = "override"
    name = "override"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        page_id: str,
        access_token: str,
        app_secret: str = "",
        verify_token: str = "",
        base_url: str = "https://graph.facebook.com/v21.0",
    ) -> None:
        if not (page_id and access_token):
            raise ValueError(f"{self.name} requires a page id and access token")
        self._page_id = page_id
        self._app_secret = app_secret
        self.verify_token = verify_token
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )

    def _send(self, recipient_id: str, text: str) -> SendResult:
        r = self._client.post(
            f"/{self._page_id}/messages",
            json={"recipient": {"id": recipient_id}, "message": {"text": text}},
        )
        r.raise_for_status()
        mid = r.json().get("message_id", "")
        return SendResult(
            provider_message_id=f"{recipient_id}:{mid}", provider_thread_id=recipient_id
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(address=f"{self.channel}:{self._page_id}",
                               provider_resource_id=self._page_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send(message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        recipient, _ = split_composite_id(provider_message_id)
        return self._send(recipient, message.text or "")

    def meta_verify(self, params: Mapping[str, str]) -> str | None:
        if (
            params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == self.verify_token
        ):
            return params.get("hub.challenge")
        return None

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        if self._app_secret:
            received = lower_headers(headers).get("x-hub-signature-256", "")
            expected = "sha256=" + hmac.new(
                self._app_secret.encode(), payload, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(received, expected):
                raise WebhookVerificationError("Meta signature mismatch")
        return parse_messaging_webhook(payload, self._page_id, self.channel)


class InstagramProvider(_MetaMessagingProvider):
    name = "instagram"
    channel = "instagram"


class FacebookProvider(_MetaMessagingProvider):
    name = "facebook"
    channel = "facebook"
