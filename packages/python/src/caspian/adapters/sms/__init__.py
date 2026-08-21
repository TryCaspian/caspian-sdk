"""Twilio SMS — form webhooks, Basic auth Messages API. Parse + plan only."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from caspian.adapters.pack import pack
from caspian.adapters.plan import http_form
from caspian.adapters.thread import decode_thread
from caspian.adapters.verify import twilio_sig
from caspian.core.commands import Command, Post, Reply, SendMedia
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result
from caspian.core.types import Attachment, Message, ThreadId

API_BASE = "https://api.twilio.com/2010-04-01"


def parse(raw: RawInbound) -> Result:
    try:
        form = parse_qs(raw.body.decode())
    except (UnicodeDecodeError, ValueError) as e:
        return Result.err(DecodeError(reason=f"Invalid form body: {e}"))

    def first(key: str) -> str:
        values = form.get(key)
        return values[0] if values else ""

    from_number = first("From")
    if not from_number:
        return Result.ok([])

    message = Message(
        thread_id=ThreadId(f"sms:{from_number}"),
        text=first("Body"),
        chat_kind="dm",
        sender=from_number,
        message_id=first("MessageSid"),
        attachments=_extract_attachments(form, first),
        raw={k: v for k, v in form.items()},
    )
    return Result.ok([message])


def _extract_attachments(
    form: dict[str, Any], first: Callable[[str], str]
) -> tuple[Attachment, ...]:
    try:
        num_media = int(first("NumMedia") or "0")
    except ValueError:
        num_media = 0
    out: list[Attachment] = []
    for i in range(num_media):
        url = first(f"MediaUrl{i}")
        if not url:
            continue
        out.append(
            Attachment(type="file", url=url, mime_type=first(f"MediaContentType{i}"))
        )
    return tuple(out)


def plan(cmd: Command, conn: Connection) -> Result:
    sid = conn.config.get("account_sid", "")
    token = conn.config.get("auth_token", "")
    if not sid or not token:
        return Result.err(
            AdapterError(
                reason="No account_sid/auth_token in connection config",
                command_tag=getattr(cmd, "tag", ""),
            )
        )
    from_number = conn.config.get("from_number", "")

    match cmd:
        case Post(thread_id=tid, text=text) | Reply(thread_id=tid, text=text):
            form = _msg_form(tid, from_number, text)
            return Result.ok(_req(sid, token, form, "sendMessage"))
        case SendMedia(thread_id=tid, attachment=att, caption=caption):
            form = _msg_form(tid, from_number, caption)
            form["MediaUrl"] = att.url or att.file_id
            return Result.ok(_req(sid, token, form, "sendMedia"))
        case _:
            return Result.err(
                AdapterError(
                    reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )


def _msg_form(tid: ThreadId, from_number: str, text: str) -> dict[str, str]:
    parts = decode_thread(tid)
    return {"To": parts[0] if parts else "", "From": from_number, "Body": text}


def _req(sid: str, token: str, form: dict[str, str], native: str):
    raw = f"{sid}:{token}".encode()
    return http_form(
        url=f"{API_BASE}/Accounts/{sid}/Messages.json",
        form=form,
        headers={"Authorization": "Basic " + base64.b64encode(raw).decode()},
        native=native,
    )


SmsAdapter = pack(channel="sms", parse=parse, plan=plan, verify=twilio_sig)
