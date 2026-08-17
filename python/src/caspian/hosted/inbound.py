"""Hosted inbound — turn normalized gateway events into kernel Events.

In hosted mode the gateway parses each platform webhook and pushes a NORMALIZED
event to the developer (via ``GET /v1/events`` polling, or by POSTing to the
developer's URL). This module maps such a normalized event JSON into the SAME
kernel ``Event`` types the rest of the SDK already produces, so identical rules
and handlers fire regardless of transport.

It also verifies the gateway's HMAC-SHA256 signature and de-dupes repeated
event ids. The parser is channel-agnostic: ``channel`` is used only to build the
hosted ``ThreadId`` convention ``f"{channel}:{conversation_id}"``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import deque
from typing import Any

from caspian.core.errors import DecodeError
from caspian.core.ports import RawInbound, Result
from caspian.core.types import (
    Action,
    Attachment,
    ChatKind,
    Deleted,
    Edited,
    Event,
    MemberJoin,
    MemberLeave,
    Message,
    Reaction,
    Receipt,
    ThreadId,
)
from caspian.hosted.client import GatewayClient, GatewayRequest

SIGNATURE_HEADER = "X-Caspian-Signature"
_ATTACHMENT_FIELDS = frozenset(
    {"type", "url", "file_id", "filename", "mime_type", "size_bytes", "caption"}
)
_CHAT_KINDS = frozenset({"dm", "group", "channel"})


class GatewayEventParser:
    """Parse a normalized gateway event payload into kernel Events."""

    def parse(self, raw: RawInbound) -> Result:
        """Decode the JSON body and map it to a list of kernel Events.

        Returns ``Result.ok([...])`` (single-event list, or ``[]`` for an
        unknown ``type``), or ``Result.err(DecodeError)`` on invalid JSON. A
        batch shape ``{"events": [...]}`` is parsed element-by-element and
        concatenated. Never raises.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))
        return self.parse_payload(payload)

    def parse_payload(self, payload: Any) -> Result:  # noqa: ANN401
        """Map an already-decoded payload (single event or batch) to Events."""
        if not isinstance(payload, dict):
            return Result.ok([])
        if isinstance(payload.get("events"), list):
            events: list[Event] = []
            for item in payload["events"]:
                if isinstance(item, dict):
                    events.extend(self._parse_one(item))
            return Result.ok(events)
        return Result.ok(self._parse_one(payload))

    # Gateway event types -> the kernel-facing kind this parser already handles.
    _TYPE_MAP = {
        "message.received": "message",
        "message.backfilled": "message",
        "message.edited": "edited",
        "interaction.received": "action",
        "reaction.received": "reaction",
    }

    def _normalise(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Flatten a gateway EventOut into the shape _parse_one expects.

        The gateway sends {id, seq, type: "message.received", data: {message: {...}}};
        this parser works in {channel, conversation_id, type: "message", message: {...}}.
        Anything already in the flat shape is returned untouched.
        """
        raw_type = str(obj.get("type", ""))
        data = obj.get("data")
        if "." not in raw_type or not isinstance(data, dict):
            return obj

        kind = self._TYPE_MAP.get(raw_type, "")
        if not kind:
            return {}  # message.sent, message.otp, ... are not inbound work

        inner = data.get("message") or data.get("interaction") or data.get("reaction") or {}
        if not isinstance(inner, dict):
            return {}
        # Never react to our own outbound copy; that is how loops start.
        if inner.get("direction") == "outbound":
            return {}

        return {
            "type": kind,
            "channel": str(inner.get("channel", "")),
            "conversation_id": str(inner.get("conversation_id", "")),
            kind if kind != "edited" else "edited": inner,
        }

    def _parse_one(self, obj: dict[str, Any]) -> list[Event]:
        obj = self._normalise(obj)
        if not obj:
            return []
        channel = str(obj.get("channel", ""))
        conversation_id = str(obj.get("conversation_id", ""))
        thread_id = ThreadId(f"{channel}:{conversation_id}")
        event_type = obj.get("type", "")

        if event_type == "message":
            return self._message(thread_id, obj.get("message") or {})
        if event_type == "action":
            return self._action(thread_id, obj.get("action") or {})
        if event_type == "reaction":
            return self._reaction(thread_id, obj.get("reaction") or {})
        if event_type == "receipt":
            return self._receipt(thread_id, obj.get("receipt") or {})
        if event_type == "member_join":
            return self._member_join(thread_id, obj.get("member_join") or {})
        if event_type == "member_leave":
            return self._member_leave(thread_id, obj.get("member_leave") or {})
        if event_type == "edited":
            return self._edited(thread_id, obj.get("edited") or {})
        if event_type == "deleted":
            return self._deleted(thread_id, obj.get("deleted") or {})
        return []

    def _message(self, thread_id: ThreadId, m: dict[str, Any]) -> list[Event]:
        chat_kind: ChatKind = "dm"
        raw_kind = m.get("chat_kind", "")
        if raw_kind in _CHAT_KINDS:
            chat_kind = raw_kind
        return [
            Message(
                thread_id=thread_id,
                text=str(m.get("text", "")),
                chat_kind=chat_kind,
                sender=str(m.get("sender", "")),
                message_id=str(m.get("id", "")),
                attachments=self._attachments(m.get("attachments")),
                reply_to=str(m.get("reply_to", "")),
            )
        ]

    def _attachments(self, items: Any) -> tuple[Attachment, ...]:  # noqa: ANN401
        if not isinstance(items, list):
            return ()
        out: list[Attachment] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = {k: v for k, v in item.items() if k in _ATTACHMENT_FIELDS}
            if "type" not in fields:
                continue
            out.append(Attachment(**fields))
        return tuple(out)

    def _action(self, thread_id: ThreadId, a: dict[str, Any]) -> list[Event]:
        return [
            Action(
                thread_id=thread_id,
                data=str(a.get("data", "")),
                sender=str(a.get("sender", "")),
                message_id=str(a.get("message_id", "")),
            )
        ]

    def _reaction(self, thread_id: ThreadId, r: dict[str, Any]) -> list[Event]:
        return [
            Reaction(
                thread_id=thread_id,
                emoji=str(r.get("emoji", "")),
                sender=str(r.get("sender", "")),
                message_id=str(r.get("message_id", "")),
                removed=bool(r.get("removed", False)),
            )
        ]

    def _receipt(self, thread_id: ThreadId, r: dict[str, Any]) -> list[Event]:
        status = r.get("status", "read")
        if status not in ("read", "delivered"):
            status = "read"
        return [
            Receipt(
                thread_id=thread_id,
                status=status,
                sender=str(r.get("sender", "")),
                message_id=str(r.get("message_id", "")),
            )
        ]

    def _member_join(self, thread_id: ThreadId, m: dict[str, Any]) -> list[Event]:
        return [MemberJoin(thread_id=thread_id, member=str(m.get("member", "")))]

    def _member_leave(self, thread_id: ThreadId, m: dict[str, Any]) -> list[Event]:
        return [MemberLeave(thread_id=thread_id, member=str(m.get("member", "")))]

    def _edited(self, thread_id: ThreadId, e: dict[str, Any]) -> list[Event]:
        return [
            Edited(
                thread_id=thread_id,
                message_id=str(e.get("message_id", "")),
                text=str(e.get("text", "")),
                sender=str(e.get("sender", "")),
            )
        ]

    def _deleted(self, thread_id: ThreadId, d: dict[str, Any]) -> list[Event]:
        return [
            Deleted(
                thread_id=thread_id,
                message_id=str(d.get("message_id", "")),
                sender=str(d.get("sender", "")),
            )
        ]


class GatewaySignatureVerifier:
    """Verify the gateway's HMAC-SHA256 signature over the raw body."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def verify(self, raw: RawInbound) -> bool:
        """Constant-time-compare the body HMAC to ``X-Caspian-Signature``.

        Returns True when no secret is configured. The header may carry an
        optional ``sha256=`` prefix.
        """
        if self._secret == "":
            return True
        expected = hmac.new(
            self._secret.encode("utf-8"), raw.body, hashlib.sha256
        ).hexdigest()
        got = raw.headers.get(SIGNATURE_HEADER, "")
        if got.startswith("sha256="):
            got = got[len("sha256=") :]
        return hmac.compare_digest(expected, got)


class DedupCache:
    """A bounded record of seen event ids. No TTL; pure-stdlib."""

    def __init__(self, max_size: int = 1024) -> None:
        self._max_size = max_size
        self._order: deque[str] = deque()
        self._seen: set[str] = set()

    def seen(self, event_id: str) -> bool:
        """Return True if ``event_id`` was already recorded."""
        return event_id in self._seen

    def record(self, event_id: str) -> None:
        """Record ``event_id``, evicting the oldest id when at capacity."""
        if event_id in self._seen:
            return
        self._seen.add(event_id)
        self._order.append(event_id)
        while len(self._order) > self._max_size:
            oldest = self._order.popleft()
            self._seen.discard(oldest)


class GatewayPoller:
    """Poll ``GET /v1/events`` and yield kernel Events, tracking the cursor."""

    def __init__(self, client: GatewayClient, cursor: str = "") -> None:
        self._client = client
        self._parser = GatewayEventParser()
        self.cursor = cursor

    def fetch_raw(self) -> Result:
        """GET /v1/events and return the batch as RawInbound (unparsed).

        The gateway answers with a JSON array of EventOut objects and pages by
        ``after_seq``/``limit`` (not a cursor token). We wrap the array in the
        batch envelope the parser understands and advance the cursor to the
        highest seq we have seen, so the next poll only asks for newer rows.
        """
        request = GatewayRequest(
            method="GET",
            path="/v1/events",
            params={"after_seq": str(self.cursor or "0"), "limit": "100"},
        )
        result = self._client.send(request)
        if not result.is_ok:
            return result

        response = result.value
        rows = response.json_list
        if not rows and isinstance(response.json_body, dict):
            # Tolerate an enveloped shape too, so a future gateway change or a
            # test double that returns {"events": [...]} keeps working.
            maybe = response.json_body.get("events")
            rows = maybe if isinstance(maybe, list) else []

        for row in rows:
            if isinstance(row, dict):
                seq = row.get("seq")
                if isinstance(seq, int) and seq > (self.cursor or 0):
                    self.cursor = seq

        return Result.ok(RawInbound(body=json.dumps({"events": rows}).encode()))

    def poll(self) -> Result:
        """Issue ``GET /v1/events`` and parse the response into Events.

        On success returns ``Result.ok(list[Event])`` and advances
        ``self.cursor`` from the response. On error the client's Result is
        propagated unchanged.
        """
        request = GatewayRequest(
            method="GET", path="/v1/events", params={"cursor": self.cursor}
        )
        result = self._client.send(request)
        if not result.is_ok:
            return result
        body = result.value.json_body
        next_cursor = body.get("cursor")
        if isinstance(next_cursor, str):
            self.cursor = next_cursor
        return self._parser.parse_payload(body)
