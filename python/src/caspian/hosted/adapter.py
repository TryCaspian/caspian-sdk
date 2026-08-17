"""GatewayAdapter — hosted mode as an adapter, so it reuses the ONE inbound pipeline.

In hosted mode the "platform" is Caspian's gateway. This adapter composes the
hosted inbound parser (gateway events → kernel Events), the hosted outbound
mapping (Commands → gateway request-descriptions), and the gateway signature
verifier — presenting the same shape as a channel adapter so `ProcessInterpreter`
runs unchanged (verify → parse → step → handlers → execute → transport). There is
no second inbound implementation; webhook and poll paths both flow through here.
"""

from __future__ import annotations

from caspian.core.commands import Command
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

    @property
    def name(self) -> str:
        return "gateway"

    def verify(self, raw: RawInbound, conn: Connection) -> bool:
        return self._verifier.verify(raw)

    def parse(self, raw: RawInbound) -> Result:
        return self._parser.parse(raw)

    def execute(self, cmd: Command, conn: Connection) -> Result:
        return self._outbound.execute(cmd, conn)

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
                "delete",
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
