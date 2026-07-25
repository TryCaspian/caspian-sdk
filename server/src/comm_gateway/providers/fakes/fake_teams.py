"""In-memory Teams provider for local development and tests.

Consumes real Bot Framework Activity shapes so tests exercise the same
normalization path as the live adapter. Zero-config by default.
"""

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)
from ..teams import COMPOSITE_SEP, SIGNATURE_HEADER, TeamsProvider, _split_teams_id, parse_activity


class FakeTeamsProvider:
    name = "fake-teams"
    channel = "teams"
    capabilities = TeamsProvider.capabilities
    connect_credentials = ()

    def __init__(self, app_secret: str = "") -> None:
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._app_secret = app_secret
        self._msg_seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"teams-bot-fake-{request.agent_id[-6:]}",
            provider_resource_id=f"fake-{secrets.randbelow(100000)}",
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        conversation_id = message.to[0] if message.to else ""
        self._msg_seq += 1
        aid = f"act_{self._msg_seq}"
        self.sent.append({
            "conversation_id": conversation_id,
            "text": message.text,
        })
        return SendResult(
            provider_message_id=f"{conversation_id}{COMPOSITE_SEP}{aid}",
            provider_thread_id=conversation_id,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        conversation_id, reply_to_id = _split_teams_id(provider_message_id)
        self._msg_seq += 1
        aid = f"act_{self._msg_seq}"
        self.replies.append({
            "conversation_id": conversation_id,
            "reply_to": reply_to_id,
            "text": message.text,
        })
        return SendResult(
            provider_message_id=f"{conversation_id}{COMPOSITE_SEP}{aid}",
            provider_thread_id=conversation_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("app_secret") or self._app_secret
        if secret:
            h = lower_headers(headers)
            received = h.get(SIGNATURE_HEADER, "")
            expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            if not received or not hmac.compare_digest(received, expected):
                raise WebhookVerificationError("HMAC signature mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        app_id = (credentials or {}).get("app_id", "fake-app")
        return parse_activity(data, app_id)

    def webhook_payload(
        self,
        *,
        text: str = "Hello from Teams",
        conversation_id: str = "19:abc123def@thread.tacv2",
        activity_id: str | None = None,
        sender_id: str = "29:user-alice-id",
        sender_name: str = "Alice Smith",
        is_group: bool = False,
        bot_name: str = "",
    ) -> dict:
        self._msg_seq += 1
        aid = activity_id or f"1720000{1000 + self._msg_seq}"
        return {
            "type": "message",
            "id": aid,
            "text": text,
            "from": {"id": sender_id, "name": sender_name},
            "conversation": {
                "id": conversation_id,
                "isGroup": is_group,
            },
            "recipient": {"id": "28:bot-id", "name": bot_name},
            "serviceUrl": "https://smba.trafficmanager.net/teams",
        }
