"""Twilio Voice — form webhooks, TwiML Say. Parse + plan only."""

from __future__ import annotations

from urllib.parse import parse_qs
from xml.sax.saxutils import escape

from caspian.adapters.pack import pack
from caspian.adapters.plan import twiml
from caspian.adapters.verify import twilio_sig
from caspian.core.commands import Command, Post, Reply
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result
from caspian.core.types import Message, ThreadId


def parse(raw: RawInbound) -> Result:
    try:
        form = parse_qs(raw.body.decode())
    except (UnicodeDecodeError, ValueError) as e:
        return Result.err(DecodeError(reason=f"Invalid form body: {e}"))

    def first(key: str) -> str:
        values = form.get(key)
        return values[0] if values else ""

    call_sid = first("CallSid")
    if not call_sid:
        return Result.ok([])

    message = Message(
        thread_id=ThreadId(f"voice:{call_sid}"),
        text=first("SpeechResult") or first("TranscriptionText") or "",
        chat_kind="dm",
        sender=first("From"),
        message_id=call_sid,
        raw={k: v for k, v in form.items()},
    )
    return Result.ok([message])


def plan(cmd: Command, conn: Connection) -> Result:
    match cmd:
        case Post(text=text) | Reply(text=text):
            markup = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<Response><Say>{text}</Say></Response>"
            )
            return Result.ok(twiml(markup=markup, native="say"))
        case _:
            return Result.err(
                AdapterError(
                    reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )


VoiceAdapter = pack(
    channel="voice",
    parse=parse,
    plan=plan,
    verify=twilio_sig,
    format=escape,
)
