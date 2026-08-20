"""GatewayAdapter — hosted mode as an adapter, so it reuses the ONE inbound pipeline.

In hosted mode the "platform" is Caspian's gateway. This adapter composes the
hosted inbound parser (gateway events → kernel Events), the hosted outbound
mapping (Commands → gateway request-descriptions), and the gateway signature
verifier — presenting the same shape as a channel adapter so `ProcessInterpreter`
runs unchanged (verify → parse → step → handlers → execute → transport). There is
no second inbound implementation; webhook and poll paths both flow through here.
"""

from __future__ import annotations

import json
from typing import Any

from caspian.core.commands import Command, Post, Reply, Typing
from caspian.core.ports import Connection, RawInbound, Result
from caspian.core.types import Event, ThreadId
from caspian.hosted.inbound import GatewayEventParser, GatewaySignatureVerifier
from caspian.hosted.outbound import GatewayOutbound


class GatewayAdapter:
    """Adapter-shaped facade over the hosted gateway (used with GatewayTransport)."""

    def __init__(self, *, webhook_secret: str = "") -> None:
        self._outbound = GatewayOutbound()
        self._parser = GatewayEventParser()
        self._verifier = GatewaySignatureVerifier(webhook_secret)
        # thread id -> id of the most recent inbound message on it. The gateway
        # keys typing off a MESSAGE ("show a typing hint in reply to this"),
        # not off a conversation, so the id has to be remembered here.
        self._last_inbound: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "gateway"

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        return self._verifier.verify(raw)

    def parse(self, raw: RawInbound) -> Result:
        result = self._parser.parse(raw)
        if result.is_ok:
            self._remember_inbound(raw)
        return result

    def _remember_inbound(self, raw: RawInbound) -> None:
        """Record the newest inbound message id per thread (best effort)."""
        try:
            payload: Any = json.loads(raw.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        rows = payload.get("events") if isinstance(payload, dict) else None
        for row in rows if isinstance(rows, list) else [payload]:
            if not isinstance(row, dict):
                continue
            message = ((row.get("data") or {}).get("message")) or {}
            if not isinstance(message, dict) or message.get("direction") != "inbound":
                continue
            mid = str(message.get("id", ""))
            thread = f"{message.get('channel', '')}:{message.get('conversation_id', '')}"
            if mid:
                self._last_inbound[thread] = mid

    def execute(self, cmd: Command, conn: Connection) -> Result:
        if isinstance(cmd, Typing):
            return self._typing(cmd)
        if isinstance(cmd, Post):
            cmd = self._as_reply(cmd)
        return self._outbound.execute(cmd, conn)

    def _as_reply(self, cmd: Post) -> Command:
        """Turn a Post into a Reply to the message that triggered this turn.

        A handler answering an inbound message means "reply to this", and on
        email that is the difference between a threaded conversation and a
        stray new message: only the reply path sets In-Reply-To/References and
        the Re: subject. Posting into a conversation is still available for
        genuinely unprompted messages (thread.send()).
        """
        if cmd.standalone:
            return cmd  # the developer explicitly asked for a new thread
        mid = self._last_inbound.get(str(cmd.thread_id), "")
        if not mid:
            return cmd  # nothing triggered this; a plain send is correct
        return Reply(
            thread_id=cmd.thread_id,
            reply_to=mid,
            text=cmd.text,
            actions=cmd.actions,
        )

    def _typing(self, cmd: Typing) -> Result:
        """POST /v1/messages/{message_id}/typing.

        Without a known message id there is nothing to hang the hint on, so this
        is a no-op rather than an error: a missing typing indicator must never
        fail the reply that follows it.
        """
        from caspian.core.ports import Sent
        from caspian.hosted.outbound import _gateway_sent

        mid = self._last_inbound.get(str(cmd.thread_id), "")
        if not mid:
            return Result.ok(Sent(raw={"noop": "typing (no inbound message id yet)"}))
        # Build through the shared helper so the request shape can never drift
        # from what GatewayTransport reads (it expects raw["gateway"]["path"]).
        return Result.ok(
            _gateway_sent("typing", "POST", f"/v1/messages/{mid}/typing", {})
        )

    def overlap_key(self, event: Event) -> str:
        return str(event.thread_id)

    def channel_of(self, event: Event) -> str:
        """Hosted thread ids are 'channel:conversation'; predicate matching uses channel."""
        return str(event.thread_id).split(":", 1)[0]

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                "receive",
                "reply",
                "send",
                "media",
                "buttons",
                "blocks",
                "edit",
                "react",
                "typing",
                "threading",
                "history",
                "modals",
            }
        )

    def format(self, text: str) -> str:
        return text

    def encode_thread(self, *parts: str) -> ThreadId:
        return ThreadId(":".join(parts))

    def decode_thread(self, thread_id: ThreadId) -> tuple[str, str]:
        channel, _, rest = str(thread_id).partition(":")
        return channel, rest
