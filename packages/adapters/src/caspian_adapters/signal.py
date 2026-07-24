# packages/adapters/src/caspian_adapters/signal.py

"""Signal adapter via local signal-cli daemon (JSON-RPC mode)."""

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .base import (
    Attachment,
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)

if TYPE_CHECKING:
    import httpx

MAX_TIMESTAMP_SKEW = 60 * 5

SIGNAL_TOKEN_HEADER = "x-signal-token"
SIGNAL_TIMESTAMP_HEADER = "x-signal-timestamp"
SIGNAL_SIGNATURE_HEADER = "x-signal-signature"


def parse_envelope(data: dict[str, Any], local_number: str) -> list[InboundMessage]:
    """Parse a Signal envelope into InboundMessage objects."""
    messages = []

    if data.get("method") == "receive":
        params = data.get("params", {})
        container = params.get("result", params)

        if isinstance(container, dict):
            envelope = (
                container.get("envelope")
                or container.get("data", {}).get("envelope")
                or {}
            )
            raw_messages = [envelope]
        else:
            raw_messages = []
    else:
        raw_messages = data.get("messages", [data])

    for msg in raw_messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") not in (None, "message", "dataMessage"):
            continue

        try:
            source = msg.get("source", "")
            message_id = msg.get("id", "unknown")
            timestamp = msg.get("timestamp", 0)
            data_msg = msg.get("dataMessage", {})
            text = data_msg.get("message", "")

            attachments = [
                Attachment(
                    url=att.get("url"),
                    mime_type=att.get("contentType"),
                    filename=att.get("filename"),
                    size_bytes=att.get("size"),
                    provider_file_id=att.get("id"),
                )
                for att in data_msg.get("attachments", [])
            ]

            if "groupInfo" in data_msg:
                group_id = data_msg["groupInfo"].get("groupId", source)
                thread_id = f"group:{group_id}"
                chat_type = "group"
            else:
                thread_id = source
                chat_type = "private"

            composite_id = f"signal:{source}:{timestamp}:{message_id}"

            messages.append(
                InboundMessage(
                    external_event_id=composite_id,
                    provider_inbox_id=local_number,
                    provider_message_id=composite_id,
                    provider_thread_id=thread_id,
                    sender_address=source,
                    sender_name=msg.get("sourceName", ""),
                    text=text,
                    chat_type=chat_type,
                    edited=msg.get("isEdit", False),
                    auto_generated=False,
                    attachments=attachments,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return messages


class SignalProvider:
    name = "signal"
    channel = "signal"

    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.GROUP_VISIBILITY,
            Capability.ATTACHMENTS,
        }
    )

    connect_credentials = (
        "signal_registered_number",
        "signal_daemon_url",
        "signal_api_token",
        "signal_signing_secret",
    )

    def __init__(
        self,
        daemon_url: str,
        registered_number: str,
        api_token: str | None = None,
        signing_secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not daemon_url or not registered_number:
            raise ValueError(
                "SIGNAL_DAEMON_URL and SIGNAL_REGISTERED_NUMBER are required"
            )

        self._daemon_url = daemon_url.rstrip("/")
        self._number = registered_number
        self._api_token = api_token
        self._signing_secret = signing_secret
        self._timeout = timeout
        self._http: httpx.Client | None = None

    def _http_client(self) -> httpx.Client:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _send_jsonrpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        client = self._http_client()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": {"account": self._number, **params},
            "id": secrets.token_hex(8),
        }

        response = client.post(f"{self._daemon_url}/jsonrpc", json=payload)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(f"Signal daemon error: {data['error']}")

        return data.get("result", {})

    def _send_message(
        self,
        recipient: str,
        text: str,
        attachments: Sequence[Attachment] | None = None,
        quote_id: str | None = None,
        quote_timestamp: int | None = None,
        quote_sender: str | None = None,
    ) -> SendResult:
        params: dict[str, Any] = {"message": text or ""}

        if recipient.startswith("group:"):
            params["groupId"] = recipient.removeprefix("group:")
        else:
            params["recipient"] = [recipient]

        if attachments:
            params["attachments"] = [
                {"url": att.url} for att in attachments if att.url
            ]

        if quote_id:
            quote: dict[str, Any] = {"id": quote_id}
            if quote_timestamp is not None:
                quote["timestamp"] = quote_timestamp
            if quote_sender is not None:
                quote["author"] = quote_sender
            params["quote"] = quote

        result = self._send_jsonrpc("send", params)
        msg_id = result.get("id", "unknown")

        return SendResult(
            provider_message_id=f"signal:{msg_id}",
            provider_thread_id=recipient,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=self._number,
            provider_resource_id=self._number,
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

        return self._send_message(
            message.to[0],
            message.text or "",
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
            # Old format: "signal:message-id" or just "message-id"
            # split_composite_id expects "head:tail" format
            if ":" in provider_message_id:
                # "signal:msg-789" -> head="signal", tail="msg-789"
                head, tail = provider_message_id.split(":", 1)
                real_msg_id = tail
            else:
                # Just the message ID
                real_msg_id = provider_message_id
            sender = None
            timestamp = None

        if sender:
            target = sender
        elif message.to:
            target = message.to[0]
        else:
            target = provider_inbox_id

        return self._send_message(
            target,
            message.text or "",
            message.attachments,
            quote_id=real_msg_id,
            quote_timestamp=timestamp,
            quote_sender=sender,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        creds = credentials or {}
        h = lower_headers(headers)

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

            if abs(time.time() - timestamp_value) > MAX_TIMESTAMP_SKEW:
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

        number = creds.get("signal_registered_number") or self._number
        return parse_envelope(data, local_number=number)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None