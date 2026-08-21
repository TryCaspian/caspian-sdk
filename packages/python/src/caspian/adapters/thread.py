"""Colon thread ids: ``{channel}:{part}:{part}``. Empty parts are dropped."""

from __future__ import annotations

from caspian.core.types import ThreadId


def encode_thread(channel: str, *parts: str) -> ThreadId:
    nonempty = [p for p in parts if p]
    return ThreadId(":".join((channel, *nonempty)))


def decode_thread(thread_id: ThreadId | str) -> tuple[str, ...]:
    parts = str(thread_id).split(":")
    return tuple(parts[1:]) if len(parts) > 1 else ()
