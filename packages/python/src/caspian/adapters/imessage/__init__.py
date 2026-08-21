"""iMessage adapter — the only code that knows the relay/bridge exists.

iMessage has no official API. Production deployments front the Messages app on a
Mac with an HTTP-JSON relay/bridge (BlueBubbles / Sendblue-style). This adapter
speaks that relay's wire format and nothing else. It stays pure: parse relay
webhook bytes into kernel Events and turn kernel Commands into request
descriptions the shared HttpTransport can dispatch.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST",
        "url": "<relay base>/api/v1/message/text",
        "json": {...},
        "headers": {...},
        "native": "<label>",
    }))
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from caspian.catalog import capabilities_of
from caspian.core.commands import Command, Post, React, Reply, SendMedia
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import Attachment, Event, Message, ThreadId

DEFAULT_RELAY = "https://relay.local"
SIGNATURE_HEADER = "X-Relay-Signature"


class IMessageAdapter:
    """Adapter for an iMessage HTTP-JSON relay/bridge."""

    @property
    def name(self) -> str:
        return "imessage"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Verify the relay webhook signature (constant-time HMAC-SHA256 hex).

        No secret configured → trust the relay (return True). Never raises.
        """
        secret = conn.config.get("webhook_secret", "")
        if not secret:
            return True
        expected = hmac.new(secret.encode(), raw.body, hashlib.sha256).hexdigest()
        got = raw.headers.get(SIGNATURE_HEADER, "")
        return hmac.compare_digest(expected, got)

    def parse(self, raw: RawInbound) -> Result:
        """Parse a relay webhook payload into kernel Events.

        Unknown payloads → empty list (parse law). Invalid JSON → DecodeError.
        Messages the relay reports as sent by us (isFromMe) are ignored.
        Never raises; never decides.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        if not isinstance(payload, dict):
            return Result.ok([])

        if payload.get("type") == "new-message" and isinstance(payload.get("data"), dict):
            return Result.ok(self._parse_relay(payload["data"]))

        if "from" in payload and "text" in payload:
            return Result.ok(self._parse_simple(payload))

        return Result.ok([])

    def _parse_relay(self, data: dict[str, Any]) -> list[Event]:
        if data.get("isFromMe"):
            return []
        handle = data.get("handle") or {}
        address = str(handle.get("address", ""))
        return [
            Message(
                thread_id=self.encode_thread(address),
                text=str(data.get("text", "")),
                chat_kind="dm",
                sender=address,
                message_id=str(data.get("guid", "")),
                raw=data,
            )
        ]

    def _parse_simple(self, payload: dict[str, Any]) -> list[Event]:
        address = str(payload.get("from", ""))
        return [
            Message(
                thread_id=self.encode_thread(address),
                text=str(payload.get("text", "")),
                chat_kind="dm",
                sender=address,
                message_id=str(payload.get("message_id", "")),
                raw=payload,
            )
        ]

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        api_key = conn.config.get("api_key", "")
        if not api_key:
            return Result.err(
                AdapterError(
                    reason="No api_key in connection config",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )

        match cmd:
            case Post(thread_id=tid, text=text):
                body = {**self._target(tid), "message": text}
                return Result.ok(self._req(conn, "text", body, "sendText"))

            case Reply(thread_id=tid, reply_to=rid, text=text):
                body = {**self._target(tid), "message": text}
                if rid:
                    body["selectedMessageGuid"] = rid
                return Result.ok(self._req(conn, "text", body, "sendReply"))

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                body = self._media_body(tid, att, caption)
                return Result.ok(self._req(conn, "attachment", body, "sendAttachment"))

            case React(thread_id=tid, message_id=mid, emoji=emoji):
                body = {
                    **self._target(tid),
                    "selectedMessageGuid": mid,
                    "reaction": emoji,
                }
                return Result.ok(self._req(conn, "reaction", body, "sendReaction"))

            case _:
                return Result.err(
                    AdapterError(
                        reason=(
                            "iMessage relay cannot execute "
                            f"{getattr(cmd, 'tag', 'unknown')}"
                        ),
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def overlap_key(self, event: Event) -> str:
        return str(event.thread_id)

    def capabilities(self) -> frozenset[str]:
        return capabilities_of(self.name)

    def format(self, text: str) -> str:
        """iMessage is plaintext; pass through unchanged."""
        return text

    def encode_thread(self, address: str) -> ThreadId:
        return ThreadId(f"imessage:{address}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _target(self, thread_id: ThreadId) -> dict[str, Any]:
        """Route to a chat guid (group) or a handle address (dm)."""
        target = self.decode_thread(thread_id)
        if ";" in target:
            return {"chatGuid": target}
        return {"address": target}

    def _media_body(
        self, tid: ThreadId, att: Attachment, caption: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            **self._target(tid),
            "attachment": att.url or att.file_id,
            "name": att.filename,
        }
        if caption or att.caption:
            body["message"] = caption or att.caption
        return body

    def _req(
        self, conn: Connection, path: str, body: dict[str, Any], native: str
    ) -> Sent:
        base = conn.config.get("relay_url", DEFAULT_RELAY)
        api_key = conn.config.get("api_key", "")
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": f"{base}/api/v1/message/{path}",
                "json": body,
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                "native": native,
            }
        )
