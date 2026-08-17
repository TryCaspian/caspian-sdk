from __future__ import annotations

from typing import Protocol

from caspian_mcp.privacy.types import SanitizeResult
from caspian_mcp.session import SessionGuard

INBOX_CAP = 20
PREVIEW_CHARS = 240


class CaspianInboxClient(Protocol):
    def list_connections(self, channel: str | None = None) -> list[dict]: ...

    def list_conversations(self, connection_id: str | None = None) -> list[dict]: ...

    def list_messages(self, conversation_id: str) -> list[dict]: ...

    def backfill(self, conversation_id: str, limit: int = 50) -> dict: ...


def as_list(payload: object) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "conversations", "connections", "messages"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def message_text(message: dict) -> str:
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text
    return ""


def conversation_id_of(row: dict) -> str:
    for key in ("id", "conversation_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def timestamp_of(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if hasattr(value, "isoformat"):
            return value.isoformat()
        text = str(value)
        if text:
            return text
    return ""


def _preview_source(conversation: dict) -> str | None:
    for key in ("last_message_text", "preview", "snippet", "last_message"):
        value = conversation.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = message_text(value)
            if nested:
                return nested
    return None


class Inbox:
    def __init__(self, client: CaspianInboxClient, session: SessionGuard) -> None:
        self.client = client
        self.session = session

    def list_inbox(self, limit: int = INBOX_CAP) -> dict:
        cap = max(1, min(limit, INBOX_CAP))
        connections = as_list(self.client.list_connections())
        channel_by_conn = {
            str(row.get("id")): str(row.get("channel") or "")
            for row in connections
            if row.get("id")
        }
        conversations = as_list(self.client.list_conversations())
        ranked = sorted(
            conversations,
            key=lambda row: timestamp_of(row, "updated_at", "created_at"),
            reverse=True,
        )[:cap]
        items: list[dict] = []
        for row in ranked:
            cid = conversation_id_of(row)
            if not cid:
                continue
            preview_raw = _preview_source(row)
            messages: list[dict] = []
            if preview_raw is None:
                messages = as_list(self.client.list_messages(cid))
                preview_raw = message_text(messages[-1]) if messages else ""
            last = messages[-1] if messages else {}
            channel = (
                str(last.get("channel") or "")
                or channel_by_conn.get(str(row.get("connection_id") or ""), "")
                or str(row.get("channel") or "")
            )
            updated = timestamp_of(last, "created_at") or timestamp_of(
                row, "updated_at", "created_at"
            )
            preview = ""
            if preview_raw:
                result = self.session.sanitize(preview_raw)
                preview = result.safe_text[:PREVIEW_CHARS]
            items.append(
                {
                    "channel": channel,
                    "conversation_id": cid,
                    "updated_at": updated,
                    "preview": preview,
                }
            )
        return self._with_mapping({"conversations": items})

    def get_thread(
        self,
        conversation_id: str,
        limit: int = 50,
        backfill: bool = False,
    ) -> dict:
        if backfill:
            self.client.backfill(conversation_id, limit=limit)
        messages = as_list(self.client.list_messages(conversation_id))
        if limit > 0:
            messages = messages[-limit:]
        joined = _format_thread(messages)
        result = self.session.sanitize(joined) if joined else _empty_result(self.session)
        return {
            "conversation_id": conversation_id,
            "safe_text": result.safe_text,
            "mapping_id": result.mapping_id,
            "redaction_report": self.session.redaction_report(result.mapping_id),
        }

    def brief_status(self, n: int = 5, m: int = 20) -> dict:
        n = max(1, min(n, INBOX_CAP))
        m = max(1, min(m, 100))
        connections = as_list(self.client.list_connections())
        channel_by_conn = {
            str(row.get("id")): str(row.get("channel") or "")
            for row in connections
            if row.get("id")
        }
        conversations = as_list(self.client.list_conversations())
        ranked = sorted(
            conversations,
            key=lambda row: timestamp_of(row, "updated_at", "created_at"),
            reverse=True,
        )[:n]
        chunks: list[str] = []
        for row in ranked:
            cid = conversation_id_of(row)
            if not cid:
                continue
            messages = as_list(self.client.list_messages(cid))[-m:]
            channel = ""
            if messages:
                channel = str(messages[-1].get("channel") or "")
            channel = (
                channel
                or channel_by_conn.get(str(row.get("connection_id") or ""), "")
                or str(row.get("channel") or "channel")
            )
            body = _format_thread(messages)
            header = f"## {channel} {cid}"
            chunks.append(f"{header}\n{body}" if body else header)
        joined = "\n\n".join(chunks)
        result = self.session.sanitize(joined) if joined else _empty_result(self.session)
        return {
            "safe_text": result.safe_text,
            "mapping_id": result.mapping_id,
            "redaction_report": self.session.redaction_report(result.mapping_id),
        }

    def _with_mapping(self, payload: dict) -> dict:
        mapping_id = self.session.mapping_id or ""
        report: dict[str, int] = {}
        if mapping_id:
            report = self.session.redaction_report(mapping_id)
        payload["mapping_id"] = mapping_id
        payload["redaction_report"] = report
        return payload


def _format_thread(messages: list[dict]) -> str:
    lines: list[str] = []
    for message in messages:
        channel = message.get("channel") or "channel"
        direction = message.get("direction") or ""
        when = timestamp_of(message, "created_at")
        text = message_text(message)
        if not text:
            continue
        lines.append(f"[{channel} {direction} {when}]\n{text}")
    return "\n\n".join(lines)


def _empty_result(session: SessionGuard) -> SanitizeResult:
    if session.mapping_id:
        return SanitizeResult(safe_text="", mapping_id=session.mapping_id)
    return session.sanitize("")
