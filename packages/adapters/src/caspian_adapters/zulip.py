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
        email, api_key = self._credentials(credentials)

        body = {
            "type": "private",
            "to": message.to[0],
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
        credentials=None,
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
def test_send(monkeypatch):
    provider = ZulipProvider()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 123}

    monkeypatch.setattr(
        provider._client,
        "post",
        lambda *args, **kwargs: Response(),
    )

    result = provider.send(
        provider_inbox_id="bot@example.com",
        message=OutboundMessage(
            text="hello",
            to=("alice@example.com",),
        ),
        credentials={
            "email": "bot@example.com",
            "api_key": "secret",
        },
    )

    assert result.provider_message_id == "123"
    assert result.provider_thread_id == "alice@example.com"
def test_reply(monkeypatch):
    provider = ZulipProvider()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 456}

    monkeypatch.setattr(
        provider._client,
        "post",
        lambda *args, **kwargs: Response(),
    )

    result = provider.reply(
        provider_inbox_id="alice@example.com",
        provider_message_id="alice@example.com",
        message=OutboundMessage(
            text="hi back",
        ),
        credentials={
            "email": "bot@example.com",
            "api_key": "secret",
        },
    )

    assert result.provider_message_id == "456"