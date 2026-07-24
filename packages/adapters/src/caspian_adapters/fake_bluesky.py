"""In-memory Bluesky provider for tests."""

import json
import secrets

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from .bluesky import BlueskyProvider, parse_notification


class FakeBlueskyProvider:
    name = "fake-bluesky"
    channel = "bluesky"
    capabilities = BlueskyProvider.capabilities
    connect_credentials = BlueskyProvider.connect_credentials

    def __init__(self) -> None:
        self.did = f"did:plc:{secrets.token_hex(12)}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        handle = creds.get("handle", "agent.bsky.social")
        return ProvisionResult(
            address=f"@{handle}", provider_resource_id=self.did
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        self.sent.append({"text": message.text})
        self._seq += 1
        uri = f"at://{self.did}/app.bsky.feed.post/{self._seq}"
        return SendResult(provider_message_id=uri, provider_thread_id=uri)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        self.replies.append({"in_reply_to": provider_message_id, "text": message.text})
        self._seq += 1
        uri = f"at://{self.did}/app.bsky.feed.post/{self._seq}"
        
        # for a real reply, the thread id is the root. Here we mock it as the target uri
        return SendResult(provider_message_id=uri, provider_thread_id=provider_message_id)

    def parse_webhook(
        self, payload: bytes, headers: dict, credentials=None
    ) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON") from exc
        
        # We don't check signature in fake
        account_did = (credentials or {}).get("provider_resource_id", self.did)
        if isinstance(data, list):
            notifs = data
        elif "notifications" in data:
            notifs = data["notifications"]
        else:
            notifs = [data]
            
        out = []
        for n in notifs:
            msg = parse_notification(n, account_did)
            if msg:
                out.append(msg)
        return out

    def webhook_payload(
        self, *, author_handle="ahuman", author_did="did:plc:123", text="Hi", reason="mention",
        uri="at://did:plc:123/app.bsky.feed.post/456", indexed_at="2026-07-24T12:00:00.000Z"
    ):
        return {
            "uri": uri,
            "cid": "bafyreihere",
            "author": {
                "did": author_did,
                "handle": author_handle,
                "displayName": "A Human",
            },
            "reason": reason,
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": indexed_at,
            },
            "isRead": False,
            "indexedAt": indexed_at,
        }
