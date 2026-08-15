"""Linear adapter — the only code that knows the Linear GraphQL API exists.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST",
        "url": "https://api.linear.app/graphql",
        "json": {"query": "...", "variables": {...}},
        "headers": {"Authorization": "<api_key>"},
        "native": "commentCreate",
    }))

parse consumes Linear webhook payloads (Comment/Issue); execute builds GraphQL
mutations. Threads encode as ``linear:<issue_id>``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from caspian.core.commands import Command, Post, Reply
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import Event, Message, ThreadId

GRAPHQL_URL = "https://api.linear.app/graphql"

_COMMENT_MUTATION = (
    "mutation($input: CommentCreateInput!)"
    "{commentCreate(input:$input){success}}"
)


class LinearAdapter:
    """Adapter for the Linear GraphQL API + webhooks."""

    @property
    def name(self) -> str:
        return "linear"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Verify the Linear-Signature header (HMAC-SHA256 hex of the body).

        When no webhook_secret is configured we cannot verify, so we accept.
        """
        secret = conn.config.get("webhook_secret", "")
        if not secret:
            return True
        got = raw.headers.get("Linear-Signature", "")
        expected = hmac.new(secret.encode(), raw.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, got)

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Linear webhook payload into kernel Events.

        Unknown payload types → empty list (parse law). Never raises.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        if not isinstance(payload, dict):
            return Result.ok([])

        kind = payload.get("type", "")
        data = payload.get("data", {}) or {}

        if kind == "Comment":
            return Result.ok(self._parse_comment(data))
        if kind == "Issue":
            return Result.ok(self._parse_issue(data))
        return Result.ok([])

    def _parse_comment(self, data: dict[str, Any]) -> list[Event]:
        issue_id = str(data.get("issue", {}).get("id", ""))
        sender = str(data.get("user", {}).get("id", ""))
        return [
            Message(
                thread_id=ThreadId(f"linear:{issue_id}"),
                text=data.get("body", ""),
                chat_kind="channel",
                sender=sender,
                message_id=str(data.get("id", "")),
                raw=data,
            )
        ]

    def _parse_issue(self, data: dict[str, Any]) -> list[Event]:
        issue_id = str(data.get("id", ""))
        return [
            Message(
                thread_id=ThreadId(f"linear:{issue_id}"),
                text=data.get("title", data.get("description", "")),
                chat_kind="channel",
                sender=str(data.get("creatorId", "")),
                message_id=issue_id,
                raw=data,
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
                return Result.ok(self._comment_req(api_key, tid, text))

            case Reply(thread_id=tid, text=text):
                return Result.ok(self._comment_req(api_key, tid, text))

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
        return frozenset({"receive", "reply", "send", "threading"})

    def format(self, text: str) -> str:
        """Linear comments accept plain Markdown; text is sent verbatim."""
        return text

    def encode_thread(self, issue_id: str) -> ThreadId:
        return ThreadId(f"linear:{issue_id}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":", 1)
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _comment_req(self, api_key: str, tid: ThreadId, text: str) -> Sent:
        issue_id = self.decode_thread(tid)
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": GRAPHQL_URL,
                "json": {
                    "query": _COMMENT_MUTATION,
                    "variables": {"input": {"issueId": issue_id, "body": text}},
                },
                "headers": {"Authorization": api_key},
                "native": "commentCreate",
            }
        )
