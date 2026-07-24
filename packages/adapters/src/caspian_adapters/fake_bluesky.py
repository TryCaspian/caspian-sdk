"""Offline fake Bluesky adapter for testing without network."""

from collections.abc import Mapping

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)


class FakeBlueskyProvider:
    name = "fake-bluesky"
    channel = "bluesky"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    connect_credentials = ("handle", "app_password")

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        handle = request.credentials.get("handle", "fake.bsky.social")
        return ProvisionResult(
            address=handle,
            provider_resource_id=f"did:plc:{handle}",
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        # In a real fake, we'd append to an internal list of sent messages
        # so tests could assert on them.
        return SendResult(provider_message_id="at://did:plc:fake/app.bsky.feed.post/fakeid")

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        return SendResult(provider_message_id="at://did:plc:fake/app.bsky.feed.post/fake_reply_id")

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        return []

    def poll_mentions(
        self, credentials: Mapping[str, str] | None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str | None]:
        return [], cursor
