"""Signal adapter over a self-hosted signal-cli daemon.

Signal has no official bot API, so this is the self-hosted path: the deployment
registers its own number and runs signal-cli in daemon mode
(`signal-cli -a +NUMBER daemon --http localhost:8080`), which exposes JSON-RPC
2.0 at POST /api/v1/rpc. We talk to that daemon.

Because a deployment owns exactly ONE registered number, this is a
deployment-level integration like the Mac mini iMessage bridge — the daemon URL,
the number, and the inbound secret are config, not per-connection credentials.

signal-cli JSON-RPC:
  - Send: {"jsonrpc":"2.0","method":"send","id":"<uuid>","params":{...}} where
    params carry EITHER {"recipient": ["+1555..."]} for a DM or
    {"groupId": "<base64>"} for a group, plus {"message": "<text>"}. The result
    is {"timestamp": 1700000000000, "results": [...]}; Signal identifies a
    message by that send timestamp, so it is our provider_message_id.
  - Inbound: the `receive` subscription streams notifications shaped
    {"jsonrpc":"2.0","method":"receive","params":{"envelope":{...},"account":...}}.
    A thin bridge forwards those to the gateway. signal-cli is a local process
    and signs nothing, so inbound is authenticated with a shared secret header
    (opt-in, like the BlueBubbles/Twilio/Meta providers).

provider_message_id is "{thread}:{timestamp}" so a reply routes without a
lookup. `thread` is the base64 groupId for group traffic and the sender's E.164
number for a DM; neither can contain ':', so partition(':') recovers it.
"""

import hmac
import json
import uuid
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
    split_composite_id,
)

SECRET_HEADER = "x-signal-secret"
RPC_PATH = "/api/v1/rpc"


def target_params(target: str) -> dict:
    """Address a send at either a DM or a group.

    signal-cli takes `recipient` for a person and `groupId` for a group. E.164
    numbers always start with '+' and base64 group ids never do, so the leading
    character tells them apart without a lookup.
    """
    if target.startswith("+"):
        return {"recipient": [target]}
    return {"groupId": target}


def parse_envelope(payload: dict, inbox_number: str) -> list[InboundMessage]:
    """Normalize one signal-cli receive notification into our schema.

    Accepts both the raw `{"envelope": ...}` object and the JSON-RPC
    notification that wraps it in `params`, since a bridge may forward either.

    Only data messages carrying text become inbound: receipts, typing
    indicators and our own linked-device sync messages have no `dataMessage`,
    and attachment- or reaction-only messages carry no text — this provider
    claims neither MEDIA nor REACTIONS, so it drops them rather than emitting an
    empty message the agent would reply to.
    """
    if "envelope" not in payload and isinstance(payload.get("params"), dict):
        payload = payload["params"]
    envelope = payload.get("envelope") or {}
    data = envelope.get("dataMessage") or {}
    text = data.get("message")
    if not text:
        return []
    sender = envelope.get("source") or envelope.get("sourceNumber")
    timestamp = envelope.get("timestamp") or data.get("timestamp")
    if not sender or not timestamp:
        return []
    group_id = (data.get("groupInfo") or {}).get("groupId")
    thread = group_id or sender
    return [
        InboundMessage(
            external_event_id=str(timestamp),
            provider_inbox_id=inbox_number,
            provider_message_id=f"{thread}:{timestamp}",
            provider_thread_id=thread,
            sender_address=sender,
            sender_name=envelope.get("sourceName"),
            recipients=[{"address": inbox_number}],
            text=text,
            chat_type="group" if group_id else "private",
        )
    ]


class SignalProvider:
    name = "signal-cli"
    channel = "signal"
    # The deployment's own registered number: nothing to collect per connection.
    connect_credentials: tuple[str, ...] = ()
    # signal-cli can message any number without prior inbound, so INITIATE is
    # honest. Group traffic is normalized but GROUP_VISIBILITY is deliberately
    # not claimed yet, nor are MEDIA/REACTIONS — none of them are implemented.
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
        }
    )

    def __init__(self, base_url: str, number: str, webhook_secret: str = "") -> None:
        if not (base_url and number):
            raise ValueError(
                "COMM_SIGNAL_CLI_URL and COMM_SIGNAL_NUMBER are required "
                "for the signal-cli provider"
            )
        self._number = number
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)

    def _rpc(self, method: str, params: dict) -> dict:
        r = self._client.post(
            RPC_PATH,
            json={"jsonrpc": "2.0", "method": method, "id": str(uuid.uuid4()), "params": params},
        )
        r.raise_for_status()
        body = r.json()
        # JSON-RPC reports failures in-band with HTTP 200, so a bare result read
        # would silently swallow them.
        if body.get("error"):
            raise RuntimeError(f"signal-cli {method} failed: {body['error']}")
        return body.get("result") or {}

    def _send_text(self, target: str, text: str) -> SendResult:
        result = self._rpc("send", {**target_params(target), "message": text})
        timestamp = result.get("timestamp", "")
        return SendResult(
            provider_message_id=f"{target}:{timestamp}",
            provider_thread_id=target,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # The address is the number signal-cli is registered to (config).
        # Connectivity validation is intentionally deferred so connect never
        # blocks on the daemon being reachable.
        return ProvisionResult(address=self._number, provider_resource_id=self._number)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send_text(message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        target, _ = split_composite_id(provider_message_id)
        return self._send_text(target, message.text or "")

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._send_text(recipient, message.text or "")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        if self._webhook_secret:
            received = {k.lower(): v for k, v in headers.items()}.get(SECRET_HEADER, "")
            if not hmac.compare_digest(received, self._webhook_secret):
                raise WebhookVerificationError("signal-cli bridge secret mismatch")
        return parse_envelope(json.loads(payload), self._number)
