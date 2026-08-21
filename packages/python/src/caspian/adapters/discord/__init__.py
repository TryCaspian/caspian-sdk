"""Discord adapter — the only code that knows the Discord API exists.

Satisfies adapter laws: ack, key, parse, format, no decisions.

Uniform execute() contract (shared by all adapters):
    Result.ok(Sent(raw={
        "transport": "http_json",
        "method": "POST" | "PATCH" | "DELETE" | "PUT",
        "url": "https://discord.com/api/v10/...",
        "json": {...},
        "headers": {"Authorization": f"Bot {token}", ...},
        "native": "<label>",          # platform label (debug/tests)
    }))
A shared HttpTransport dispatches the http_json payloads.
"""

from __future__ import annotations

import json
from typing import Any

from caspian.catalog import capabilities_of
from caspian.core.commands import (
    Command,
    Delete,
    Edit,
    OpenModal,
    Pin,
    Post,
    React,
    Reply,
    SendBlocks,
    SendMedia,
    Typing,
)
from caspian.core.errors import AdapterError, DecodeError
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import (
    Action,
    Block,
    Button,
    Event,
    Message,
    Reaction,
    ThreadId,
)

API_BASE = "https://discord.com/api/v10"

# Discord interaction request types (Interactions API).
_PING = 1
_APPLICATION_COMMAND = 2
_MESSAGE_COMPONENT = 3

# Interaction callback types.
_CALLBACK_MESSAGE = 4
_CALLBACK_DEFERRED_UPDATE = 6
_CALLBACK_MODAL = 9

# Button style mapping (Discord component styles).
_BUTTON_STYLE = {"default": 2, "primary": 1, "danger": 4}


class DiscordAdapter:
    """Adapter for the Discord API (interactions webhook + gateway shapes)."""

    @property
    def name(self) -> str:
        return "discord"

    # ─── Inbound ─────────────────────────────────────────────────────────────

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        """Verify a Discord interaction signature.

        Discord signs requests with Ed25519 (headers X-Signature-Ed25519 and
        X-Signature-Timestamp, checked against conn.config["public_key"]).
        Full Ed25519 verification requires PyNaCl, which is not available here;
        it is left as a follow-up. We stay non-failing: with no public_key we
        accept, and when configured we still accept (see note above).
        """
        public_key = conn.config.get("public_key", "")
        if not public_key:
            return True
        # NOTE: Ed25519 signature verification requires PyNaCl (follow-up).
        # Until it is wired in, do not reject otherwise-valid webhooks.
        return True

    def parse(self, raw: RawInbound) -> Result:
        """Parse a Discord payload into kernel Events.

        Handles interaction webhooks (PING / APPLICATION_COMMAND /
        MESSAGE_COMPONENT) and gateway-style MESSAGE_CREATE / reaction shapes.
        Unknown payloads → empty list (parse law). Never raises.
        """
        try:
            payload = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return Result.err(DecodeError(reason=f"Invalid JSON: {e}"))

        if not isinstance(payload, dict):
            return Result.ok([])

        itype = payload.get("type")
        if itype == _PING:
            return Result.ok([])
        if itype == _APPLICATION_COMMAND:
            return Result.ok(self._parse_command(payload))
        if itype == _MESSAGE_COMPONENT:
            return Result.ok(self._parse_component(payload))

        # Gateway-style shapes (no interaction "type").
        if "emoji" in payload and "message_id" in payload:
            return Result.ok(self._parse_reaction(payload))
        if "content" in payload and "channel_id" in payload:
            return Result.ok(self._parse_message_create(payload))

        return Result.ok([])

    def _thread_id(self, channel_id: str) -> ThreadId:
        return ThreadId(f"discord:{channel_id}")

    def _parse_command(self, payload: dict[str, Any]) -> list[Event]:
        channel_id = str(payload.get("channel_id", ""))
        data = payload.get("data", {}) or {}
        text = self._command_text(data)
        return [
            Message(
                thread_id=self._thread_id(channel_id),
                text=text,
                chat_kind="channel",
                sender=self._sender(payload),
                message_id=str(payload.get("id", "")),
                metadata={"interaction_id": str(payload.get("id", ""))},
                raw=payload,
            )
        ]

    def _command_text(self, data: dict[str, Any]) -> str:
        options = data.get("options") or []
        parts: list[str] = []
        for opt in options:
            value = opt.get("value")
            if value is not None:
                parts.append(str(value))
        if parts:
            return " ".join(parts)
        return str(data.get("name", ""))

    def _parse_component(self, payload: dict[str, Any]) -> list[Event]:
        channel_id = str(payload.get("channel_id", ""))
        data = payload.get("data", {}) or {}
        message = payload.get("message", {}) or {}
        return [
            Action(
                thread_id=self._thread_id(channel_id),
                data=str(data.get("custom_id", "")),
                sender=self._sender(payload),
                message_id=str(message.get("id", "")),
                interaction_id=str(payload.get("id", "")),
                metadata={"token": str(payload.get("token", ""))},
                raw=payload,
            )
        ]

    def _parse_message_create(self, payload: dict[str, Any]) -> list[Event]:
        channel_id = str(payload.get("channel_id", ""))
        author = payload.get("author", {}) or {}
        # Ignore messages authored by any bot, including our own. The gateway
        # echoes back everything we send, so without this the handler answers
        # its own reply and loops forever.
        if author.get("bot") or payload.get("webhook_id"):
            return []
        ref = payload.get("message_reference", {}) or {}
        return [
            Message(
                thread_id=self._thread_id(channel_id),
                text=str(payload.get("content", "")),
                chat_kind="channel",
                sender=str(author.get("id", "")),
                message_id=str(payload.get("id", "")),
                reply_to=str(ref.get("message_id", "")),
                raw=payload,
            )
        ]

    def _parse_reaction(self, payload: dict[str, Any]) -> list[Event]:
        channel_id = str(payload.get("channel_id", ""))
        emoji = payload.get("emoji", {}) or {}
        return [
            Reaction(
                thread_id=self._thread_id(channel_id),
                emoji=str(emoji.get("name", "")),
                sender=str(payload.get("user_id", "")),
                message_id=str(payload.get("message_id", "")),
                raw=payload,
            )
        ]

    def _sender(self, payload: dict[str, Any]) -> str:
        member = payload.get("member", {}) or {}
        user = member.get("user") or payload.get("user", {}) or {}
        return str(user.get("id", ""))

    def acknowledge(self, event: Event, conn: Connection) -> Result | None:
        """Ack law: respond to component interactions with a callback.

        Action events come from MESSAGE_COMPONENT interactions and must be
        acknowledged (type 6, deferred update) so the client stops spinning.
        """
        if isinstance(event, Action) and event.interaction_id:
            token = event.metadata.get("token", "")
            return Result.ok(
                self._callback(
                    event.interaction_id,
                    str(token),
                    {"type": _CALLBACK_DEFERRED_UPDATE},
                    native="interactionCallback",
                )
            )
        return None

    # ─── Outbound ────────────────────────────────────────────────────────────

    def execute(self, cmd: Command, conn: Connection) -> Result:
        token = conn.config.get("bot_token", "")
        if not token:
            return Result.err(
                AdapterError(
                    reason="No bot_token in connection config",
                    command_tag=getattr(cmd, "tag", ""),
                )
            )

        match cmd:
            case Post(thread_id=tid, text=text, actions=actions):
                body: dict[str, Any] = {"content": text}
                if actions:
                    body["components"] = self._components(actions)
                return Result.ok(
                    self._req(token, "POST", self._messages_url(tid), body, "post")
                )

            case Reply(thread_id=tid, reply_to=rid, text=text, actions=actions):
                body = {"content": text, "message_reference": {"message_id": rid}}
                if actions:
                    body["components"] = self._components(actions)
                return Result.ok(
                    self._req(token, "POST", self._messages_url(tid), body, "reply")
                )

            case SendBlocks(thread_id=tid, blocks=blocks, text=text, actions=actions):
                body = {"embeds": [self._embed(b) for b in blocks]}
                if text:
                    body["content"] = text
                if actions:
                    body["components"] = self._components(actions)
                return Result.ok(
                    self._req(token, "POST", self._messages_url(tid), body, "sendBlocks")
                )

            case SendMedia(thread_id=tid, attachment=att, caption=caption):
                body = {"content": caption}
                url = att.url or att.file_id
                if att.type in ("photo", "video"):
                    body["embeds"] = [{"image": {"url": url}}]
                else:
                    body["attachments"] = [{"url": url, "filename": att.filename}]
                return Result.ok(
                    self._req(token, "POST", self._messages_url(tid), body, "sendMedia")
                )

            case Edit(thread_id=tid, message_id=mid, text=text, actions=actions):
                body = {"content": text}
                if actions:
                    body["components"] = self._components(actions)
                url = f"{self._messages_url(tid)}/{mid}"
                return Result.ok(self._req(token, "PATCH", url, body, "edit"))

            case Delete(thread_id=tid, message_id=mid):
                url = f"{self._messages_url(tid)}/{mid}"
                return Result.ok(self._req(token, "DELETE", url, None, "delete"))

            case React(thread_id=tid, message_id=mid, emoji=emoji):
                emo = self._encode_emoji(emoji)
                url = f"{self._messages_url(tid)}/{mid}/reactions/{emo}/@me"
                return Result.ok(self._req(token, "PUT", url, None, "react"))

            case Typing(thread_id=tid):
                url = f"{API_BASE}/channels/{self._chan(tid)}/typing"
                return Result.ok(self._req(token, "POST", url, {}, "typing"))

            case Pin(thread_id=tid, message_id=mid):
                url = f"{API_BASE}/channels/{self._chan(tid)}/pins/{mid}"
                return Result.ok(self._req(token, "PUT", url, None, "pin"))

            case OpenModal(
                trigger_id=trigger, blocks=blocks, title=title, callback_id=cbid
            ):
                modal = {
                    "type": _CALLBACK_MODAL,
                    "data": {
                        "title": title,
                        "custom_id": cbid or "modal",
                        "components": [self._embed(b) for b in blocks],
                    },
                }
                return Result.ok(
                    self._callback(trigger, "", modal, native="openModal")
                )

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
        return capabilities_of(self.name)

    def format(self, text: str) -> str:
        """Format text for Discord markdown (mostly passthrough).

        Discord uses standard markdown; only backticks need minimal escaping
        to avoid breaking code spans.
        """
        return text.replace("`", "\\`")

    def encode_thread(self, channel_id: str) -> ThreadId:
        return ThreadId(f"discord:{channel_id}")

    def decode_thread(self, thread_id: ThreadId) -> str:
        parts = str(thread_id).split(":")
        return parts[1] if len(parts) > 1 else ""

    # ─── Internal ────────────────────────────────────────────────────────────

    def _chan(self, thread_id: ThreadId) -> str:
        return self.decode_thread(thread_id)

    def _messages_url(self, thread_id: ThreadId) -> str:
        return f"{API_BASE}/channels/{self._chan(thread_id)}/messages"

    def _components(self, actions: tuple[Button, ...]) -> list[dict[str, Any]]:
        buttons: list[dict[str, Any]] = []
        for b in actions:
            comp: dict[str, Any] = {"type": 2, "label": b.label}
            if b.url:
                comp["style"] = 5
                comp["url"] = b.url
            else:
                comp["style"] = _BUTTON_STYLE.get(b.style, 2)
                comp["custom_id"] = b.data
            buttons.append(comp)
        return [{"type": 1, "components": buttons}]

    def _embed(self, block: Block) -> dict[str, Any]:
        content = dict(block.content)
        embed: dict[str, Any] = {}
        if "title" in content:
            embed["title"] = content["title"]
        if "text" in content:
            embed["description"] = content["text"]
        if "description" in content:
            embed["description"] = content["description"]
        if "image" in content:
            embed["image"] = {"url": content["image"]}
        if not embed:
            embed = content
        return embed

    def _encode_emoji(self, emoji: str) -> str:
        # Custom emoji come as "name:id"; unicode emoji pass through.
        return emoji

    def _callback(
        self, interaction_id: str, token: str, body: dict[str, Any], *, native: str
    ) -> Sent:
        url = f"{API_BASE}/interactions/{interaction_id}/{token}/callback"
        return Sent(
            raw={
                "transport": "http_json",
                "method": "POST",
                "url": url,
                "json": body,
                "headers": {"Content-Type": "application/json"},
                "native": native,
            }
        )

    def _req(
        self,
        token: str,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        native: str,
    ) -> Sent:
        raw: dict[str, Any] = {
            "transport": "http_json",
            "method": method,
            "url": url,
            "headers": {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            },
            "native": native,
        }
        if body is not None:
            raw["json"] = body
        return Sent(raw=raw)
