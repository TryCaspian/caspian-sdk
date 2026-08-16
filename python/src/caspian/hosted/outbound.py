"""Outbound mapping — kernel Command → gateway request-description.

In hosted mode the same kernel Commands are routed to the gateway's /v1 API
instead of the platform. This mirrors an adapter's execute() but the target is
Caspian's gateway. It emits the uniform Sent(raw=...) with transport="gateway";
GatewayTransport dispatches it via a GatewayClient.

Hosted ThreadId convention: f"{channel}:{gateway_conversation_id}". The part
after the first colon is the gateway conversation id.
"""

from __future__ import annotations

from typing import Any

from caspian.core.commands import (
    Command,
    Delete,
    Edit,
    Forward,
    Initiate,
    ListHistory,
    MarkRead,
    OpenModal,
    Pin,
    Post,
    React,
    Reply,
    ScheduleSend,
    SendBlocks,
    SendMedia,
    Typing,
    Unpin,
)
from caspian.core.errors import AdapterError
from caspian.core.ports import Connection, Result, Sent
from caspian.core.types import Button, ThreadId


def conversation_id(thread_id: ThreadId) -> str:
    """Extract the gateway conversation id from a hosted thread id."""
    parts = str(thread_id).split(":", 1)
    return parts[1] if len(parts) > 1 else str(thread_id)


def _buttons(actions: tuple[Button, ...]) -> list[dict[str, Any]]:
    return [
        {"label": b.label, "data": b.data, "url": b.url, "style": b.style}
        for b in actions
    ]


def _gateway_sent(native: str, method: str, path: str, body: dict[str, Any]) -> Sent:
    return Sent(
        raw={
            "transport": "gateway",
            "native": native,
            "gateway": {
                "method": method,
                "path": path,
                "json_body": body,
            },
        }
    )


def _gateway_get(native: str, path: str, params: dict[str, str]) -> Sent:
    return Sent(
        raw={
            "transport": "gateway",
            "native": native,
            "gateway": {
                "method": "GET",
                "path": path,
                "params": params,
            },
        }
    )


class GatewayOutbound:
    """Maps kernel Commands to gateway /v1 request-descriptions (hosted execute).

    Extendable: unmapped commands return an AdapterError so callers see the gap
    rather than a silent no-op.
    """

    def execute(self, cmd: Command, conn: Connection) -> Result:
        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "postMessage",
                        "POST",
                        f"/v1/conversations/{cid}/messages",
                        {"text": text, "actions": _buttons(actions)},
                    )
                )

            case Initiate(thread_id=tid, text=text, actions=actions):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "initiate",
                        "POST",
                        f"/v1/connections/{conn.id}/initiate",
                        {"conversation": cid, "text": text, "actions": _buttons(actions)},
                    )
                )

            case Reply(reply_to=mid, text=text, actions=actions):
                return Result.ok(
                    _gateway_sent(
                        "reply",
                        "POST",
                        f"/v1/messages/{mid}/reply",
                        {"text": text, "actions": _buttons(actions)},
                    )
                )

            case Edit(message_id=mid, text=text):
                return Result.ok(
                    _gateway_sent("edit", "POST", f"/v1/messages/{mid}/edit", {"text": text})
                )

            case React(message_id=mid, emoji=emoji):
                return Result.ok(
                    _gateway_sent("react", "POST", f"/v1/messages/{mid}/react", {"emoji": emoji})
                )

            case Typing(thread_id=tid):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "typing", "POST", f"/v1/conversations/{cid}/typing", {}
                    )
                )

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "sendMedia",
                        "POST",
                        f"/v1/conversations/{cid}/messages",
                        {
                            "media": {
                                "type": att.type,
                                "url": att.url,
                                "file_id": att.file_id,
                                "filename": att.filename,
                            },
                            "text": caption,
                        },
                    )
                )

            case Delete(message_id=mid):
                return Result.ok(
                    _gateway_sent("delete", "POST", f"/v1/messages/{mid}/delete", {})
                )

            case Pin(message_id=mid):
                return Result.ok(
                    _gateway_sent("pin", "POST", f"/v1/messages/{mid}/pin", {})
                )

            case Unpin(message_id=mid):
                return Result.ok(
                    _gateway_sent("unpin", "POST", f"/v1/messages/{mid}/unpin", {})
                )

            case Forward(to_thread_id=to_tid, message_id=mid):
                return Result.ok(
                    _gateway_sent(
                        "forward",
                        "POST",
                        f"/v1/messages/{mid}/forward",
                        {"to": conversation_id(to_tid)},
                    )
                )

            case MarkRead(thread_id=tid, message_id=mid):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "markRead",
                        "POST",
                        f"/v1/conversations/{cid}/read",
                        {"message_id": mid},
                    )
                )

            case ScheduleSend(thread_id=tid, text=text, send_at=send_at, actions=actions):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "schedule",
                        "POST",
                        f"/v1/conversations/{cid}/messages",
                        {"text": text, "actions": _buttons(actions), "send_at": send_at},
                    )
                )

            case SendBlocks(thread_id=tid, blocks=blocks, text=text, actions=actions):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_sent(
                        "sendBlocks",
                        "POST",
                        f"/v1/conversations/{cid}/messages",
                        {
                            "text": text,
                            "blocks": [b.model_dump() for b in blocks],
                            "actions": _buttons(actions),
                        },
                    )
                )

            case OpenModal(
                trigger_id=trigger_id, blocks=blocks, title=title, callback_id=callback_id
            ):
                return Result.ok(
                    _gateway_sent(
                        "openModal",
                        "POST",
                        f"/v1/interactions/{trigger_id}/modal",
                        {
                            "blocks": [b.model_dump() for b in blocks],
                            "title": title,
                            "callback_id": callback_id,
                        },
                    )
                )

            case ListHistory(thread_id=tid, limit=limit, before=before):
                cid = conversation_id(tid)
                return Result.ok(
                    _gateway_get(
                        "listHistory",
                        f"/v1/conversations/{cid}/backfill",
                        {"limit": str(limit), "before": before},
                    )
                )

            case _:
                return Result.err(
                    AdapterError(
                        reason=f"Hosted outbound does not map command {getattr(cmd, 'tag', '?')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )
