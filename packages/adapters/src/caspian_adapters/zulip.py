"""Zulip adapter."""

import json

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


def parse_event(data: dict, bot_email: str):
    if data.get("type") != "message":
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
            chat_type=(
                "stream"
                if data.get("message_type") == "stream"
                else "private"
            ),
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
        self._client = httpx.Client(base_url=base_url, timeout=30.0)
    def parse_webhook(
        self,
        payload: bytes,
        headers,
        credentials=None,
    ):
        if credentials is None:
            raise WebhookVerificationError(
                "zulip webhooks require a connection scope"
            )

        secret = credentials.get("webhook_secret")
        received = headers.get("x-zulip-webhook-secret")

        if secret and received != secret:
            raise WebhookVerificationError(
                "webhook secret mismatch"
            )

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError(
                "invalid JSON payload"
            ) from exc

        return parse_event(
            data,
            credentials["email"],
        )
    def provision(
        self,
        request: ProvisionRequest,
    ) -> ProvisionResult:
        return ProvisionResult(
            address=request.credentials["email"],
            provider_resource_id=request.credentials["email"],
        )
    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        raise NotImplementedError
    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        raise NotImplementedError