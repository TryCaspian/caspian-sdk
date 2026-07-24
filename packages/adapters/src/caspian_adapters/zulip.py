"""Zulip adapter."""

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
)


def parse_event(data: dict, bot_email: str) -> list[InboundMessage]:
    if data.get("type") not in ("stream", "private"):
        return []

    return [
        InboundMessage(
            external_event_id=str(data["id"]),
            provider_inbox_id=bot_email,
            provider_message_id=str(data["id"]),
            provider_thread_id=str(
                data.get("stream_id") or data.get("recipient_id")
            ),
            sender_address=data.get("sender_email"),
            sender_name=data.get("sender_full_name"),
            text=data.get("content"),
            chat_type=data.get("type"),
        )
    ]


class ZulipProvider:
    name = "zulip"
    channel = "zulip"

    connect_credentials = (
        "email",
        "api_key",
        "webhook_secret",
    )

    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    def __init__(
        self,
        base_url: str = "https://zulip.com/api/v1",
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=30.0,
        )

    @staticmethod
    def _credentials(
        credentials: Mapping[str, str] | None,
    ) -> tuple[str, str]:
        if credentials is None:
            raise ValueError(
                "connection is missing email/api_key credentials"
            )

        return (
            credentials["email"],
            credentials["api_key"],
        )

    def provision(
        self,
        request: ProvisionRequest,
    ) -> ProvisionResult:
        return ProvisionResult(
            address=request.credentials["email"],
            provider_resource_id=request.credentials["email"],
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        if credentials is None:
            raise WebhookVerificationError(
                "zulip webhooks require a connection scope"
            )

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError(
                "invalid JSON payload"
            ) from exc

        token = data.get("token")
        if token != credentials.get("webhook_secret"):
            raise WebhookVerificationError(
                "token mismatch"
            )

        message = data.get("message")
        if message is None:
            raise WebhookVerificationError(
                "missing message payload"
            )

        return parse_event(
            message,
            credentials["email"],
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        email, api_key = self._credentials(credentials)

        body = {
            "type": "private",
            "to": list(message.to),
            "content": message.text or "",
        }

        if message.subject:
            body["type"] = "stream"
            body["topic"] = message.subject

        response = self._client.post(
            "/messages",
            json=body,
            auth=(email, api_key),
        )
        response.raise_for_status()

        result = response.json()

        return SendResult(
            provider_message_id=str(result["id"]),
            provider_thread_id=str(body["to"]),
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        email, api_key = self._credentials(credentials)

        body = {
            "type": "private",
            "to": provider_inbox_id,
            "content": message.text or "",
        }

        if message.subject:
            body["type"] = "stream"
            body["topic"] = message.subject
            body["to"] = provider_message_id

        response = self._client.post(
            "/messages",
            json=body,
            auth=(email, api_key),
        )
        response.raise_for_status()

        result = response.json()

        return SendResult(
            provider_message_id=str(result["id"]),
            provider_thread_id=str(body["to"]),
        )