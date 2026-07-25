"""Signal channel adapter (via signal-cli JSON-RPC).

Supports local daemon connection via Unix domain socket, TCP socket, or HTTP.
Inbound messages arrive via webhook or simulated webhook parsing.
"""

import json
import socket
import time
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


class SignalProvider:
    name = "signal"
    channel = "signal"
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )
    connect_credentials = ()

    def __init__(
        self,
        number: str = "",
        socket_path: str = "",
        tcp_address: str = "",
        http_url: str = "",
        webhook_secret: str = "",
    ) -> None:
        self._number = number
        self._socket_path = socket_path
        self._tcp_address = tcp_address
        self._http_url = http_url
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(timeout=30.0)

    def _read_line(self, s: socket.socket) -> str:
        buffer = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            if b"\n" in chunk:
                break
        line, _, _ = buffer.partition(b"\n")
        return line.decode("utf-8")

    def _query(self, method: str, params: dict) -> dict:
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": "1",
        }
        payload = (json.dumps(req) + "\n").encode("utf-8")

        if self._socket_path:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(30.0)
                s.connect(self._socket_path)
                s.sendall(payload)
                res = self._read_line(s)
                if not res:
                    raise RuntimeError("Empty response from Signal Unix domain socket")
                return json.loads(res)
        elif self._tcp_address:
            host, _, port_str = self._tcp_address.partition(":")
            port = int(port_str) if port_str else 7583
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(30.0)
                s.connect((host, port))
                s.sendall(payload)
                res = self._read_line(s)
                if not res:
                    raise RuntimeError("Empty response from Signal TCP socket")
                return json.loads(res)
        elif self._http_url:
            r = self._client.post(self._http_url, json=req)
            r.raise_for_status()
            return r.json()
        else:
            raise ValueError("No connection settings configured for Signal provider")

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        number = (request.credentials or {}).get("number") or self._number
        if not number:
            raise ValueError("Signal provider requires a registered number")
        return ProvisionResult(address=number, provider_resource_id=number)

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        if not message.to:
            raise ValueError("Signal message must have at least one recipient")
        recipient = message.to[0]
        number = (credentials or {}).get("number") or self._number
        params = {
            "message": message.text or "",
            "recipient": [recipient],
        }
        if number:
            params["account"] = number

        res = self._query("send", params)
        if "error" in res:
            raise RuntimeError(f"Signal send failed: {res['error']}")

        timestamp = res.get("result", {}).get("timestamp") or int(time.time() * 1000)
        return SendResult(
            provider_message_id=f"{recipient}:{timestamp}:{number}",
            provider_thread_id=recipient,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        chat_id, tail = split_composite_id(provider_message_id)
        timestamp_str, _, author = tail.partition(":")

        number = (credentials or {}).get("number") or self._number

        params = {
            "message": message.text or "",
            "recipient": [chat_id],
        }
        if number:
            params["account"] = number

        if timestamp_str and author:
            try:
                timestamp = int(timestamp_str)
                params["quoteTimestamp"] = timestamp
                params["quoteAuthor"] = author
            except ValueError:
                pass

        res = self._query("send", params)
        if "error" in res:
            raise RuntimeError(f"Signal reply failed: {res['error']}")

        new_timestamp = res.get("result", {}).get("timestamp") or int(time.time() * 1000)
        return SendResult(
            provider_message_id=f"{chat_id}:{new_timestamp}:{number}",
            provider_thread_id=chat_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("webhook_secret") or self._webhook_secret
        if secret:
            received = lower_headers(headers).get("x-signal-webhook-token")
            if received != secret:
                raise WebhookVerificationError("Signal webhook token mismatch")

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc

        envelope = None
        account = None
        if isinstance(data, dict):
            if "envelope" in data:
                envelope = data["envelope"]
                account = data.get("account")
            elif (
                "params" in data
                and isinstance(data["params"], dict)
                and "envelope" in data["params"]
            ):
                envelope = data["params"]["envelope"]
                account = data["params"].get("account")
            else:
                envelope = data

        if not isinstance(envelope, dict):
            return []

        data_msg = envelope.get("dataMessage")
        if not data_msg and "syncMessage" in envelope:
            data_msg = envelope["syncMessage"].get("sentMessage", {}).get("dataMessage")
        if not data_msg:
            return []

        text = data_msg.get("message")
        if not text:
            return []

        sender_address = (
            envelope.get("source")
            or envelope.get("sourceNumber")
            or envelope.get("sourceUuid")
        )
        sender_name = envelope.get("sourceName")
        timestamp = (
            data_msg.get("timestamp")
            or envelope.get("timestamp")
            or int(time.time() * 1000)
        )

        group_info = data_msg.get("groupInfo") or {}
        group_id = group_info.get("groupId")

        if group_id:
            chat_id = group_id if group_id.startswith("group.") else f"group.{group_id}"
            chat_type = "group"
        else:
            chat_id = sender_address
            chat_type = "private"

        inbox_id = account or (credentials or {}).get("number") or self._number or "signal"

        return [
            InboundMessage(
                external_event_id=f"{inbox_id}:{timestamp}",
                provider_inbox_id=inbox_id,
                provider_message_id=f"{chat_id}:{timestamp}:{sender_address}",
                provider_thread_id=chat_id,
                sender_address=sender_address,
                sender_name=sender_name,
                text=text,
                chat_type=chat_type,
            )
        ]


class FakeSignalProvider:
    name = "fake-signal"
    channel = "signal"
    capabilities = SignalProvider.capabilities
    connect_credentials = ()

    def __init__(self, number: str = "+1234567890", webhook_secret: str = "") -> None:
        self._number = number or "+1234567890"
        self._webhook_secret = webhook_secret
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._update_seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        number = (request.credentials or {}).get("number") or self._number
        return ProvisionResult(
            address=number,
            provider_resource_id=number,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        recipient = message.to[0] if message.to else ""
        self.sent.append({
            "account": provider_inbox_id,
            "recipient": recipient,
            "message": message.text or "",
        })
        timestamp = int(time.time() * 1000)
        return SendResult(
            provider_message_id=f"{recipient}:{timestamp}:{self._number}",
            provider_thread_id=recipient,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        chat_id, tail = split_composite_id(provider_message_id)
        timestamp_str, _, author = tail.partition(":")

        self.replies.append({
            "account": provider_inbox_id,
            "recipient": chat_id,
            "message": message.text or "",
            "quote": {
                "timestamp": timestamp_str,
                "author": author,
            }
        })
        timestamp = int(time.time() * 1000)
        return SendResult(
            provider_message_id=f"{chat_id}:{timestamp}:{self._number}",
            provider_thread_id=chat_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        provider = SignalProvider(number=self._number, webhook_secret=self._webhook_secret)
        return provider.parse_webhook(payload, headers, credentials)

    def webhook_payload(
        self,
        *,
        sender: str = "+33123456789",
        sender_name: str = "Alice",
        text: str = "Hi there",
        group_id: str | None = None,
        timestamp: int | None = None,
    ) -> dict:
        self._update_seq += 1
        ts = timestamp if timestamp is not None else int(time.time() * 1000)

        envelope = {
            "source": sender,
            "sourceNumber": sender,
            "sourceUuid": f"uuid-{sender[-4:]}",
            "sourceName": sender_name,
            "timestamp": ts,
            "dataMessage": {
                "timestamp": ts,
                "message": text,
                "expiresInSeconds": 0,
                "viewOnce": False,
            }
        }

        if group_id:
            envelope["dataMessage"]["groupInfo"] = {
                "groupId": group_id,
                "type": "DELIVER",
            }

        return {
            "jsonrpc": "2.0",
            "method": "receive",
            "params": {
                "account": self._number,
                "envelope": envelope,
            }
        }
