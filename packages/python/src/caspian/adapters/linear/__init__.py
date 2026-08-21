"""Linear adapter — GraphQL comments on issues. Parse + plan only."""

from __future__ import annotations

import json
from typing import Any

from caspian.adapters.pack import pack
from caspian.adapters.plan import http_json
from caspian.adapters.thread import decode_thread
from caspian.adapters.verify import hmac_hex
from caspian.core.commands import Command, Post, Reply
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result
from caspian.core.types import Event, Message, ThreadId

GRAPHQL_URL = "https://api.linear.app/graphql"

_COMMENT_MUTATION = (
    "mutation($input: CommentCreateInput!)"
    "{commentCreate(input:$input){success}}"
)


def parse(raw: RawInbound) -> Result:
    try:
        payload = json.loads(raw.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

    if not isinstance(payload, dict):
        return Result.ok([])

    kind = payload.get("type", "")
    data = payload.get("data", {}) or {}

    if kind == "Comment":
        return Result.ok(_parse_comment(data))
    if kind == "Issue":
        return Result.ok(_parse_issue(data))
    return Result.ok([])


def _parse_comment(data: dict[str, Any]) -> list[Event]:
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


def _parse_issue(data: dict[str, Any]) -> list[Event]:
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


def plan(cmd: Command, conn: Connection) -> Result:
    api_key = conn.config.get("api_key", "")
    if not api_key:
        return Result.err(
            AdapterError(
                reason="No api_key in connection config",
                command_tag=getattr(cmd, "tag", ""),
            )
        )

    match cmd:
        case Post(thread_id=tid, text=text) | Reply(thread_id=tid, text=text):
            return Result.ok(_comment_req(api_key, tid, text))
        case _:
            return Result.err(
                AdapterError(
                    reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )


def _comment_req(api_key: str, tid: ThreadId, text: str):
    parts = decode_thread(tid)
    issue_id = parts[0] if parts else ""
    return http_json(
        url=GRAPHQL_URL,
        json={
            "query": _COMMENT_MUTATION,
            "variables": {"input": {"issueId": issue_id, "body": text}},
        },
        headers={"Authorization": api_key},
        native="commentCreate",
    )


LinearAdapter = pack(
    channel="linear",
    parse=parse,
    plan=plan,
    verify=hmac_hex(header="Linear-Signature", secret_key="webhook_secret"),
)
