from typing import Mapping

from ..base import Capability, InboundMessage, OutboundMessage, ProvisionRequest, ProvisionResult, SendResult
from ..bluesky import BlueskyProvider


class FakeBlueskyProvider(BlueskyProvider):
    name = "fake-bluesky"

    def __init__(self):
        super().__init__(identifier="fake", password="fake", base_url="http://fake.local")
        self.outbound: list[OutboundMessage] = []
        self.inbound: list[InboundMessage] = []
        self.replies: list[tuple[str, OutboundMessage]] = []

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address="fake.bsky.social",
            provider_resource_id="did:plc:fake123",
        )

    def send(self, provider_inbox_id: str, message: OutboundMessage, credentials=None) -> SendResult:
        self.outbound.append(message)
        mid = f"at://did:plc:fake123/app.bsky.feed.post/fakecid|fakecid|at://did:plc:fake123/app.bsky.feed.post/fakecid|fakecid"
        return SendResult(
            provider_message_id=mid,
            provider_thread_id="at://did:plc:fake123/app.bsky.feed.post/fakecid",
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        self.replies.append((provider_message_id, message))
        return SendResult(
            provider_message_id=f"{provider_message_id}_reply",
            provider_thread_id=provider_message_id,
        )

    def poll_dms(
        self, credentials: Mapping[str, str] | None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str]:
        if not self.inbound:
            return [], cursor or "1970-01-01T00:00:00.000Z"
        ret = list(self.inbound)
        self.inbound.clear()
        return ret, "new_cursor"

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        return []
