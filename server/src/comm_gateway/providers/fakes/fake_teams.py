"""In-memory Microsoft Teams provider using Bot Framework Activity shapes."""

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
)
from ..teams import TeamsProvider, _message_id, _split_message_id, parse_activity, teams_message_activity


class FakeTeamsProvider:
    name = "fake-teams"
    channel = "teams"
    capabilities = TeamsProvider.capabilities
    connect_credentials = ()
    optional_connect_credentials = ("app_id",)

    def __init__(self) -> None:
        self.app_id = f"fake-app-{secrets.token_hex(4)}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def _app_id(self, credentials: Mapping[str, str] | None) -> str:
        return (credentials or {}).get("app_id") or self.app_id

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        app_id = self._app_id(request.credentials)
        return ProvisionResult(address=f"teams:{app_id}", provider_resource_id=app_id)

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        conversation_id = message.to[0]
        self.sent.append({"conversation": conversation_id, "text": message.text})
        return SendResult(
            provider_message_id=_message_id(conversation_id, f"fake-{self._next()}"),
            provider_thread_id=conversation_id,
        )

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        conversation_id, activity_id = _split_message_id(provider_message_id)
        self.replies.append(
            {"conversation": conversation_id, "reply_to": activity_id, "text": message.text}
        )
        return SendResult(
            provider_message_id=_message_id(conversation_id, f"fake-{self._next()}"),
            provider_thread_id=conversation_id,
        )

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_activity(data, self._app_id(credentials))

    def webhook_payload(self, *, conversation_id="19:abc@thread.tacv2", text="Hi there"):
        return teams_message_activity(conversation_id, text, activity_id=f"msg-{self._next()}")

    def _next(self) -> int:
        self._seq += 1
        return self._seq
