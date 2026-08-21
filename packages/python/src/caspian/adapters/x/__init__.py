"""X / Twitter adapter — the only code that knows the X API v2 exists.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST",
        "url": "https://api.twitter.com/2/...",
        "json": {...},
        "headers": {"Authorization": "Bearer ..."},
        "native": "createTweet" | "createDm",
    }))

parse consumes X Account Activity webhook payloads; execute builds v2 request
descriptions. Threads encode as ``x:<tweet_user_id>`` for tweets and
``x:dm:<user_id>`` for direct-message conversations so execute can route by the
thread-id prefix parts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from caspian.catalog import capabilities_of
from caspian.core.commands import Command, Post, Reply
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import Event, Message, ThreadId

API_BASE = "https://api.twitter.com/2"


class XAdapter:
    """Adapter for the X (Twitter) API v2 + Account Activity webhooks."""

    @property
    def name(self) -> str:
        return "x"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Best-effort Account Activity signature check (HMAC-SHA256).

        X signs webhook bodies as ``sha256=<base64>`` using the app's consumer
        secret. When no consumer_secret is configured we cannot verify, so we
        accept (the runner/transport layer may enforce stricter checks).
        """
        secret = conn.config.get("consumer_secret", "")
        if not secret:
            return True
        got = raw.headers.get("X-Twitter-Webhooks-Signature", "")
        digest = hmac.new(secret.encode(), raw.body, hashlib.sha256).digest()
        expected = "sha256=" + base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, got)

    def parse(self, raw: RawInbound) -> Result:
        """Parse an X Account Activity payload into kernel Events.

        Unknown payload shapes → empty list (parse law). Never raises.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        if not isinstance(payload, dict):
            return Result.ok([])

        events: list[Event] = []
        for dm in payload.get("direct_message_events", []) or []:
            events.extend(self._parse_dm(dm))
        for tweet in payload.get("tweet_create_events", []) or []:
            events.extend(self._parse_tweet(tweet))
        if "dm" in payload:
            events.extend(self._parse_simple_dm(payload["dm"]))

        return Result.ok(events)

    def _parse_dm(self, dm: dict[str, Any]) -> list[Event]:
        create = dm.get("message_create", {})
        if not create:
            return []
        sender = str(create.get("sender_id", ""))
        text = create.get("message_data", {}).get("text", "")
        return [
            Message(
                thread_id=ThreadId(f"x:dm:{sender}"),
                text=text,
                chat_kind="dm",
                sender=sender,
                message_id=str(dm.get("id", "")),
                raw=dm,
            )
        ]

    def _parse_tweet(self, tweet: dict[str, Any]) -> list[Event]:
        user_id = str(tweet.get("user", {}).get("id", ""))
        return [
            Message(
                thread_id=ThreadId(f"x:{user_id}"),
                text=tweet.get("text", ""),
                chat_kind="channel",
                sender=user_id,
                message_id=str(tweet.get("id", "")),
                raw=tweet,
            )
        ]

    def _parse_simple_dm(self, dm: dict[str, Any]) -> list[Event]:
        sender = str(dm.get("from", ""))
        return [
            Message(
                thread_id=ThreadId(f"x:dm:{sender}"),
                text=dm.get("text", ""),
                chat_kind="dm",
                sender=sender,
                raw=dm,
            )
        ]

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        token = conn.config.get("bearer_token", "")
        if not token:
            return Result.err(
                AdapterError(
                    reason="No bearer_token in connection config",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )

        match cmd:
            case Post(thread_id=tid, text=text):
                kind, target = self.decode_thread(tid)
                if kind == "dm":
                    return Result.ok(self._dm_req(token, target, text))
                return Result.ok(self._tweet_req(token, {"text": text}))

            case Reply(thread_id=tid, reply_to=rid, text=text):
                kind, target = self.decode_thread(tid)
                if kind == "dm":
                    return Result.ok(self._dm_req(token, target, text))
                body = {"text": text, "reply": {"in_reply_to_tweet_id": rid}}
                return Result.ok(self._tweet_req(token, body))

            case _:
                return Result.err(
                    AdapterError(
                        reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def overlap_key(self, event: Event) -> str:
        return str(event.thread_id)

    def capabilities(self) -> frozenset[str]:
        return capabilities_of(self.name)

    def format(self, text: str) -> str:
        """X has no markup; text is sent verbatim."""
        return text

    def encode_thread(self, target_id: str, kind: str = "tweet") -> ThreadId:
        if kind == "dm":
            return ThreadId(f"x:dm:{target_id}")
        return ThreadId(f"x:{target_id}")

    def decode_thread(self, thread_id: ThreadId) -> tuple[str, str]:
        parts = str(thread_id).split(":")
        if len(parts) >= 3 and parts[1] == "dm":
            return "dm", parts[2]
        target = parts[1] if len(parts) > 1 else ""
        return "tweet", target

    # ─── Internal ────────────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _tweet_req(self, token: str, body: dict[str, Any]) -> Sent:
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": f"{API_BASE}/tweets",
                "json": body,
                "headers": self._headers(token),
                "native": "createTweet",
            }
        )

    def _dm_req(self, token: str, target: str, text: str) -> Sent:
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": f"{API_BASE}/dm_conversations/with/{target}/messages",
                "json": {"text": text},
                "headers": self._headers(token),
                "native": "createDm",
            }
        )
