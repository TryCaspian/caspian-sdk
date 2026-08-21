"""Slack adapter — the only code that knows the Slack Web API + Events API exist.

Satisfies adapter laws: verify, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST",
        "url": "https://slack.com/api/<method>",
        "json": {...},
        "headers": {"Authorization": "Bearer xoxb-...", "Content-Type": ...},
        "native": "<slack method>",
    }))
A shared HttpTransport dispatches the http_json payload.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from caspian.adapters.pack import pack
from caspian.adapters.verify import hmac_slack
from caspian.core.commands import (
    Call,
    Command,
    Delete,
    Edit,
    ListHistory,
    OpenModal,
    Post,
    React,
    Reply,
    SendBlocks,
    SendMedia,
    Typing,
    UpdateModal,
)
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import (
    Action,
    Attachment,
    Block,
    Button,
    ChatKind,
    Event,
    Message,
    Reaction,
    ThreadId,
)

API_BASE = "https://slack.com/api"


class _Slack:

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Slack Events API / interactive payload into kernel Events.

        Unknown types → empty list (parse law). Never raises.
        """
        try:
            payload = self._decode(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        ptype = payload.get("type", "")

        if ptype == "url_verification":
            # Handshake handled by the interpreter (challenge echo) — no events.
            return Result.ok([])
        if ptype == "event_callback":
            return Result.ok(self._parse_event(payload.get("event", {})))
        if ptype == "block_actions":
            return Result.ok(self._parse_block_actions(payload))

        return Result.ok([])

    def _decode(self, body: bytes | str) -> dict[str, Any]:
        text = self._text(body)
        try:
            data: dict[str, Any] = json.loads(text)
            return data
        except json.JSONDecodeError:
            parsed = urllib.parse.parse_qs(text)
            if "payload" in parsed:
                payload: dict[str, Any] = json.loads(parsed["payload"][0])
                return payload
            raise

    def _text(self, body: bytes | str) -> str:
        return body.decode("utf-8") if isinstance(body, bytes) else body

    def _thread_id(self, channel: str, thread_ts: str = "") -> ThreadId:
        if thread_ts:
            return ThreadId(f"slack:{channel}:{thread_ts}")
        return ThreadId(f"slack:{channel}")

    def _chat_kind(self, event: dict[str, Any]) -> ChatKind:
        ct = event.get("channel_type", "")
        if ct == "im":
            return "dm"
        if ct in ("mpim", "group"):
            return "group"
        return "channel"

    def _parse_event(self, event: dict[str, Any]) -> list[Event]:
        etype = event.get("type", "")
        if etype in ("message", "app_mention"):
            # Skip bot echoes and edit/delete subtypes (parse-law: no decisions).
            if event.get("bot_id") or event.get("subtype"):
                return []
            return [self._message(event)]
        if etype in ("reaction_added", "reaction_removed"):
            return [self._reaction(event, removed=etype == "reaction_removed")]
        return []

    def _message(self, event: dict[str, Any]) -> Message:
        channel = str(event.get("channel", ""))
        thread_ts = str(event.get("thread_ts", ""))
        return Message(
            thread_id=self._thread_id(channel, thread_ts),
            text=event.get("text", ""),
            chat_kind=self._chat_kind(event),
            sender=str(event.get("user", "")),
            message_id=str(event.get("ts", "")),
            attachments=self._extract_attachments(event),
            reply_to=thread_ts,
            topic_id=thread_ts,
            raw=event,
        )

    def _extract_attachments(self, event: dict[str, Any]) -> tuple[Attachment, ...]:
        out: list[Attachment] = []
        for f in event.get("files", []) or []:
            out.append(
                Attachment(
                    type=self._file_kind(f.get("mimetype", "")),
                    url=str(f.get("url_private", "")),
                    file_id=str(f.get("id", "")),
                    filename=str(f.get("name", "")),
                    mime_type=str(f.get("mimetype", "")),
                    size_bytes=int(f.get("size", 0) or 0),
                )
            )
        return tuple(out)

    def _file_kind(self, mime: str) -> str:
        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        return "file"

    def _reaction(self, event: dict[str, Any], *, removed: bool) -> Reaction:
        item = event.get("item", {})
        return Reaction(
            thread_id=self._thread_id(str(item.get("channel", ""))),
            emoji=str(event.get("reaction", "")),
            sender=str(event.get("user", "")),
            message_id=str(item.get("ts", "")),
            removed=removed,
            raw=event,
        )

    def _parse_block_actions(self, payload: dict[str, Any]) -> list[Event]:
        actions = payload.get("actions", [])
        if not actions:
            return []
        action = actions[0]
        data = action.get("action_id") or action.get("value", "")
        channel = payload.get("channel", {}).get("id", "")
        message = payload.get("message", {})
        thread_ts = str(message.get("thread_ts", ""))
        return [
            Action(
                thread_id=self._thread_id(str(channel), thread_ts),
                data=str(data),
                sender=str(payload.get("user", {}).get("id", "")),
                message_id=str(message.get("ts", "")),
                interaction_id=str(payload.get("trigger_id", "")),
                raw=payload,
            )
        ]

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        token = conn.config.get("bot_token", "")
        if not token:
            return Result.err(AdapterError(reason="No bot_token in connection config"))

        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                channel, _ = self.decode_thread(tid)
                body: dict[str, Any] = {"channel": channel, "text": text}
                if actions:
                    body["blocks"] = self._actions_blocks(text, actions)
                return Result.ok(self._req(token, "chat.postMessage", body))

            case Reply(thread_id=tid, reply_to=rid, text=text, actions=actions):
                channel, thread_ts = self.decode_thread(tid)
                body = {"channel": channel, "text": text}
                parent = rid or thread_ts
                if parent:
                    body["thread_ts"] = parent
                if actions:
                    body["blocks"] = self._actions_blocks(text, actions)
                return Result.ok(self._req(token, "chat.postMessage", body))

            case SendBlocks(thread_id=tid, blocks=blocks, text=text, actions=actions):
                channel, thread_ts = self.decode_thread(tid)
                rendered = self._render_blocks(blocks)
                if actions:
                    rendered.append(self._actions_block(actions))
                body = {"channel": channel, "blocks": rendered}
                if text:
                    body["text"] = text
                if thread_ts:
                    body["thread_ts"] = thread_ts
                return Result.ok(self._req(token, "chat.postMessage", body))

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                channel, _ = self.decode_thread(tid)
                body = {
                    "channel_id": channel,
                    "filename": att.filename or "file",
                    "title": caption or att.caption,
                }
                if att.url:
                    body["file"] = att.url
                return Result.ok(self._req(token, "files.upload_v2", body))

            case Edit(thread_id=tid, message_id=mid, text=text, actions=actions):
                channel, _ = self.decode_thread(tid)
                body = {"channel": channel, "ts": mid, "text": text}
                if actions:
                    body["blocks"] = self._actions_blocks(text, actions)
                return Result.ok(self._req(token, "chat.update", body))

            case Delete(thread_id=tid, message_id=mid):
                channel, _ = self.decode_thread(tid)
                body = {"channel": channel, "ts": mid}
                return Result.ok(self._req(token, "chat.delete", body))

            case React(thread_id=tid, message_id=mid, emoji=emoji):
                channel, _ = self.decode_thread(tid)
                body = {"channel": channel, "timestamp": mid, "name": emoji.strip(":")}
                return Result.ok(self._req(token, "reactions.add", body))

            case OpenModal(trigger_id=trig, blocks=blocks, title=title, callback_id=cid):
                body = {"trigger_id": trig, "view": self._view(blocks, title, cid)}
                return Result.ok(self._req(token, "views.open", body))

            case UpdateModal(view_id=vid, blocks=blocks, title=title):
                body = {"view_id": vid, "view": self._view(blocks, title, "")}
                return Result.ok(self._req(token, "views.update", body))

            case ListHistory(thread_id=tid, limit=limit):
                channel, _ = self.decode_thread(tid)
                body = {"channel": channel, "limit": limit}
                return Result.ok(self._req(token, "conversations.history", body))

            case Typing():
                # Slack Web API has no bot typing indicator; report unsupported.
                return Result.err(
                    AdapterError(
                        reason="Slack Web API cannot send typing indicators for bots",
                        command_tag="Typing",
                    )
                )

            case Call(method=method, args=args):
                return Result.ok(self._req(token, method, dict(args)))

            case _:
                return Result.err(
                    AdapterError(
                        reason=f"Unsupported command: {getattr(cmd, 'tag', 'unknown')}",
                        command_tag=getattr(cmd, "tag", ""),
                    )
                )

    def format(self, text: str) -> str:
        """Escape text for Slack mrkdwn (& < > are the reserved characters)."""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def encode_thread(self, channel: str, thread_ts: str = "") -> ThreadId:
        return self._thread_id(channel, thread_ts)

    def decode_thread(self, thread_id: ThreadId) -> tuple[str, str]:
        parts = str(thread_id).split(":")
        channel = parts[1] if len(parts) > 1 else ""
        thread_ts = parts[2] if len(parts) > 2 else ""
        return channel, thread_ts

    # ─── Internal ────────────────────────────────────────────────────────────

    def _actions_blocks(
        self, text: str, actions: tuple[Button, ...]
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if text:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": text}}
            )
        blocks.append(self._actions_block(actions))
        return blocks

    def _actions_block(self, actions: tuple[Button, ...]) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        for b in actions:
            el: dict[str, Any] = {
                "type": "button",
                "text": {"type": "plain_text", "text": b.label},
                "action_id": b.data or b.label,
            }
            if b.data:
                el["value"] = b.data
            if b.url:
                el["url"] = b.url
            if b.style in ("primary", "danger"):
                el["style"] = b.style
            elements.append(el)
        return {"type": "actions", "elements": elements}

    def _render_blocks(self, blocks: tuple[Block, ...]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for b in blocks:
            out.append({"type": b.type, **dict(b.content)})
        return out

    def _view(
        self, blocks: tuple[Block, ...], title: str, callback_id: str
    ) -> dict[str, Any]:
        view: dict[str, Any] = {
            "type": "modal",
            "title": {"type": "plain_text", "text": title or "Modal"},
            "blocks": self._render_blocks(blocks),
        }
        if callback_id:
            view["callback_id"] = callback_id
        return view

    def _req(self, token: str, method: str, body: dict[str, Any]) -> Sent:
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": f"{API_BASE}/{method}",
                "json": body,
                "headers": {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                "native": method,
            }
        )


_impl = _Slack()
SlackAdapter = pack(
    channel="slack",
    parse=_impl.parse,
    plan=_impl.execute,
    verify=hmac_slack,
    format=_impl.format,
    encode_thread=_impl.encode_thread,
    decode_thread=_impl.decode_thread,
)
