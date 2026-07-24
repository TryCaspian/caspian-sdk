"""Signal adapter via local signal-cli daemon (JSON-RPC 2.0 interface).

The deployment owns a registered Signal number. The adapter communicates
only with a local signal-cli daemon running in JSON-RPC mode (--json-rpc over socket/HTTP).

- provision resolves/returns the registered number
- inbound envelope payloads arrive via webhook/receive stream and normalize to InboundMessage
- outbound sends/replies dispatch JSON-RPC 2.0 requests ("method": "send") to the daemon
"""

import hmac
import json
from collections.abc import Mapping

import httpx

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
    split_composite_id,
)

SECRET_HEADER = "x-signal-secret-token"


def parse_attachments(attachments_data: list[dict]) -> list[Attachment]:
    out: list[Attachment] = []
    for att in attachments_data:
        out.append(
            Attachment(
                mime_type=att.get("contentType"),
                filename=att.get("filename"),
                size_bytes=att.get("size"),
                provider_file_id=att.get("id"),
            )
        )
    return out


def parse_envelope(data: dict, local_number: str = "") -> list[InboundMessage]:
    """Normalize a Signal envelope payload into our schema.

    Ignores sync messages (sent by self on linked devices) and self-authored messages
    to avoid echo loops. Handles text, media attachments, and quotes/replies.
    """
    envelope = data.get("envelope") or data.get("data", {})
    if not isinstance(envelope, dict):
        return []
    if "syncMessage" in envelope:
        return []
    sender = envelope.get("source") or envelope.get("sourceNumber") or ""
    if local_number and sender == local_number:
        return []
    data_msg = envelope.get("dataMessage")
    if not isinstance(data_msg, dict):
        return []
    text = data_msg.get("message")
    attachments = parse_attachments(data_msg.get("attachments") or [])
    if text is None and not attachments:
        return []
    timestamp = str(data_msg.get("timestamp") or envelope.get("timestamp") or "")
    group_info = data_msg.get("groupInfo") or {}
    group_id = group_info.get("groupId")
    if group_id:
        chat_type = "group"
        thread_id = f"group:{group_id}"
        msg_id = f"group:{group_id}:{timestamp}"
    else:
        chat_type = "private"
        thread_id = sender
        msg_id = f"{sender}:{timestamp}"

    account = data.get("account") or local_number
    sender_name = envelope.get("sourceName")
    return [
        InboundMessage(
            external_event_id=f"{sender}:{timestamp}",
            provider_inbox_id=account,
            provider_message_id=msg_id,
            provider_thread_id=thread_id,
            sender_address=sender or None,
            sender_name=sender_name or None,
            text=text,
            chat_type=chat_type,
            attachments=attachments,
        )
    ]


class SignalProvider:
    name = "signal"
    channel = "signal"
    connect_credentials = ("number",)
    optional_connect_credentials = ("daemon_url", "webhook_secret")
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.ATTACHMENTS,
        }
    )

    def __init__(
        self,
        number: str = "",
        daemon_url: str = "http://127.0.0.1:8080",
        webhook_secret: str = "",
    ) -> None:
        self._number = number
        self._daemon_url = daemon_url.rstrip("/")
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(base_url=self._daemon_url, timeout=30.0)

    def _get_number(self, credentials: Mapping[str, str] | None) -> str:
        num = (credentials or {}).get("number") or self._number
        if not num:
            raise ValueError("connection is missing a valid number credential")
        return num

    def _rpc_call(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": "1"}
        r = self._client.post("/api/v1/rpc", json=payload)
        r.raise_for_status()
        res = r.json()
        if "error" in res:
            err = res["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"signal-cli RPC {method} failed: {msg}")
        return res.get("result") or {}

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        num = self._get_number(request.credentials)
        return ProvisionResult(address=num, provider_resource_id=num)

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        number = self._get_number(credentials)
        target = message.to[0] if message.to else ""
        params: dict = {"account": number, "message": message.text or ""}
        if target.startswith("group:"):
            params["groupId"] = target.partition("group:")[2]
            thread_id = target
        else:
            params["recipient"] = [target]
            thread_id = target
        res = self._rpc_call("send", params)
        timestamp = str(res.get("timestamp") or "")
        return SendResult(
            provider_message_id=f"{target}:{timestamp}",
            provider_thread_id=thread_id,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        number = self._get_number(credentials)
        head, tail = split_composite_id(provider_message_id)
        params: dict = {"account": number, "message": message.text or ""}
        if head == "group":
            _, group_id, quote_ts = provider_message_id.split(":", 2)
            params["groupId"] = group_id
            params["quoteTimestamp"] = int(quote_ts) if quote_ts.isdigit() else quote_ts
            thread_id = f"group:{group_id}"
            target_key = f"group:{group_id}"
        else:
            params["recipient"] = [head]
            params["quoteTimestamp"] = int(tail) if tail.isdigit() else tail
            params["quoteAuthor"] = head
            thread_id = head
            target_key = head
        res = self._rpc_call("send", params)
        timestamp = str(res.get("timestamp") or "")
        return SendResult(
            provider_message_id=f"{target_key}:{timestamp}",
            provider_thread_id=thread_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("webhook_secret") or self._webhook_secret
        if secret:
            received = lower_headers(headers).get(SECRET_HEADER) or ""
            if not hmac.compare_digest(received, secret):
                raise WebhookVerificationError("secret token mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        number = (credentials or {}).get("number") or self._number
        return parse_envelope(data, local_number=number)
