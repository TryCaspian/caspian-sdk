"""Telegram adapter — the only code that knows Bot API exists.

Satisfies adapter laws: ack, key, parse, format, no decisions.
"""

from __future__ import annotations

import json
from typing import Any

from caspian.core.commands import Call, Command, Edit, Post, React, Typing
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import (
    Action,
    Event,
    Message,
    ThreadId,
)


class TelegramAdapter:
    """Adapter for Telegram Bot API.

    parse: Update bytes → Events
    execute: Command → Bot API call
    overlap_key: chat_id
    """

    @property
    def name(self) -> str:
        return "telegram"

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Telegram Update into kernel Events.

        Unknown update types → empty list (parse law).
        Never raises (error returned as Result.err).
        """
        try:
            update = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        events: list[Event] = []

        if "message" in update:
            msg = update["message"]
            chat = msg.get("chat", {})
            chat_id = str(chat.get("id", ""))
            chat_type = chat.get("type", "private")

            chat_kind = "dm" if chat_type == "private" else (
                "channel" if chat_type == "channel" else "group"
            )
            sender = msg.get("from", {})
            sender_id = str(sender.get("id", ""))

            thread_id = ThreadId(f"telegram:{chat_id}")
            text = msg.get("text", "")

            events.append(
                Message(
                    thread_id=thread_id,
                    text=text,
                    chat_kind=chat_kind,
                    sender=sender_id,
                    raw=msg,
                )
            )

        elif "callback_query" in update:
            cb = update["callback_query"]
            chat = cb.get("message", {}).get("chat", {})
            chat_id = str(chat.get("id", ""))
            sender = cb.get("from", {})
            sender_id = str(sender.get("id", ""))

            thread_id = ThreadId(f"telegram:{chat_id}")
            data = cb.get("data", "")

            events.append(
                Action(
                    thread_id=thread_id,
                    data=data,
                    sender=sender_id,
                    raw=cb,
                )
            )

        # Unknown update type → empty list (parse law)
        return Result.ok(events)

    def execute(self, cmd: Command, conn: Connection) -> Result:
        """Execute a command via Telegram Bot API.

        In Process mode this makes real HTTP calls.
        In Memory mode the runner intercepts before this is called.
        """
        token = conn.config.get("bot_token", "")
        if not token:
            return Result.err(AdapterError(reason="No bot_token in connection config"))

        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                chat_id = self._decode_chat_id(tid)
                payload: dict[str, Any] = {
                    "method": "sendMessage",
                    "chat_id": chat_id,
                    "text": text,
                }
                if actions:
                    payload["reply_markup"] = self._build_keyboard(actions)
                return Result.ok(Sent(raw=payload))

            case Edit(thread_id=tid, message_id=mid, text=text):
                chat_id = self._decode_chat_id(tid)
                payload = {
                    "method": "editMessageText",
                    "chat_id": chat_id,
                    "message_id": mid,
                    "text": text,
                }
                return Result.ok(Sent(raw=payload))

            case React(thread_id=tid, message_id=mid, emoji=emoji):
                chat_id = self._decode_chat_id(tid)
                payload = {
                    "method": "setMessageReaction",
                    "chat_id": chat_id,
                    "message_id": mid,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                }
                return Result.ok(Sent(raw=payload))

            case Typing(thread_id=tid):
                chat_id = self._decode_chat_id(tid)
                payload = {
                    "method": "sendChatAction",
                    "chat_id": chat_id,
                    "action": "typing",
                }
                return Result.ok(Sent(raw=payload))

            case Call(method=method, args=args):
                payload = {"method": method, **args}
                return Result.ok(Sent(raw=payload))

            case _:
                return Result.err(
                    AdapterError(
                        reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def overlap_key(self, event: Event) -> str:
        """Telegram overlap key is the chat_id (from thread_id)."""
        tid = str(event.thread_id)  # type: ignore[union-attr]
        return tid

    def capabilities(self) -> frozenset[str]:
        return frozenset({"receive", "reply", "send", "buttons", "edit", "react"})

    def encode_thread(self, chat_id: str) -> ThreadId:
        return ThreadId(f"telegram:{chat_id}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        return str(thread_id).removeprefix("telegram:")

    # ─── Internal ────────────────────────────────────────────────────────────

    def _decode_chat_id(self, thread_id: ThreadId) -> str:
        return str(thread_id).removeprefix("telegram:")

    def _build_keyboard(self, actions: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        buttons = []
        for act in actions:
            buttons.append({
                "text": act.get("label", act.get("text", "")),
                "callback_data": act.get("data", act.get("value", "")),
            })
        return {"inline_keyboard": [buttons]}
