"""In-memory fake Signal provider for offline testing."""

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping, Sequence

from .base import (
    Attachment,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from .signal import (
    SIGNAL_SIGNATURE_HEADER,
    SIGNAL_TIMESTAMP_HEADER,
    SIGNAL_TOKEN_HEADER,
    SignalProvider,
    parse_envelope,
)


class FakeSignalProvider:
    name = "fake-signal"
    channel = "signal"
    capabilities = SignalProvider.capabilities
    connect_credentials = SignalProvider.connect_credentials

    def __init__(
        self,
        registered_number: str = "+1234567890",
        api_token: str | None = "fake-token",
        signing_secret: str | None = None,
    ) -> None:
        self.msisdn = registered_number
        self._api_token = api_token
        self._signing_secret = signing_secret
        self.sent: list[dict] = []
        self._message_counter = 0

    def _record(
        self,
        to_number: str,
        text: str | None,
        attachments: Sequence[Attachment] | None = None,
        reply_to: str | None = None,
        reply_timestamp: int | None = None,
        reply_sender: str | None = None,
    ) -> SendResult:
        self._message_counter += 1
        msg_id = f"fake-{self._message_counter:06d}"

        attachment_dicts = [
            {
                "url": a.url,
                "mime_type": a.mime_type,
                "filename": a.filename,
                "size_bytes": a.size_bytes,
                "provider_file_id": a.provider_file_id,
            }
            for a in (attachments or [])
        ]

        record = {
            "from": self.msisdn,
            "to": to_number,
            "text": text,
            "attachments": attachment_dicts,
        }

        if reply_to:
            record["reply_to"] = reply_to
            record["reply_timestamp"] = reply_timestamp
            record["reply_sender"] = reply_sender

        self.sent.append(record)

        return SendResult(
            provider_message_id=f"signal:{msg_id}",
            provider_thread_id=to_number,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=self.msisdn,
            provider_resource_id=self.msisdn,
            provider_pod_id=None,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        if not message.to:
            raise ValueError("No recipient specified in message.to")

        return self._record(
            message.to[0],
            message.text,
            message.attachments,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        parts = provider_message_id.split(":", 3)

        if len(parts) >= 4 and parts[0] == "signal":
            sender = parts[1]
            try:
                timestamp = int(parts[2])
            except ValueError:
                timestamp = None
            real_msg_id = parts[3]
        else:
            _, _, real_msg_id = provider_message_id.partition(":")
            sender = None
            timestamp = None

        if sender:
            target = sender
        elif message.to:
            target = message.to[0]
        else:
            target = provider_inbox_id

        return self._record(
            target,
            message.text,
            message.attachments,
            reply_to=real_msg_id,
            reply_timestamp=timestamp,
            reply_sender=sender,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        creds = credentials or {}
        h = {k.lower(): v for k, v in headers.items()}

        api_token = creds.get("signal_api_token") or self._api_token
        signing_secret = creds.get("signal_signing_secret") or self._signing_secret

        if api_token:
            received_token = h.get(SIGNAL_TOKEN_HEADER, "")
            if not hmac.compare_digest(
                received_token.encode(), api_token.encode()
            ):
                raise WebhookVerificationError("Invalid webhook token")

        if signing_secret:
            timestamp = h.get(SIGNAL_TIMESTAMP_HEADER, "")
            signature = h.get(SIGNAL_SIGNATURE_HEADER, "")

            try:
                timestamp_value = int(timestamp)
            except (ValueError, TypeError):
                raise WebhookVerificationError(
                    "Webhook timestamp missing or invalid"
                ) from None

            if abs(time.time() - timestamp_value) > 300:
                raise WebhookVerificationError("Webhook timestamp too old")

            signed_payload = f"v1:{timestamp}:".encode() + payload
            expected = "v1=" + hmac.new(
                signing_secret.encode(),
                signed_payload,
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected.encode(), signature.encode()):
                raise WebhookVerificationError("Webhook signature mismatch")

        try:
            data = json.loads(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise WebhookVerificationError("Invalid JSON payload") from exc

        if not isinstance(data, dict):
            raise WebhookVerificationError("Invalid JSON payload")

        if data.get("type") == "url_verification":
            return []

        number = creds.get("signal_registered_number") or self.msisdn
        return parse_envelope(data, local_number=number)

    def webhook_payload(
        self,
        *,
        from_number: str = "+15551112222",
        text: str = "Hello from Signal!",
        message_id: str | None = None,
        group_id: str | None = None,
        attachments: list[dict] | None = None,
        edited: bool = False,
    ) -> dict:
        msg_id = message_id or f"msg_{secrets.token_hex(8)}"
        data_message = {"message": text}

        if group_id:
            data_message["groupInfo"] = {"groupId": group_id}

        if attachments:
            data_message["attachments"] = attachments

        payload = {
            "id": msg_id,
            "source": from_number,
            "sourceName": "Test User",
            "timestamp": int(time.time()),
            "dataMessage": data_message,
        }

        if edited:
            payload["isEdit"] = True

        return payload

    def clear(self) -> None:
        self.sent.clear()
        self._message_counter = 0