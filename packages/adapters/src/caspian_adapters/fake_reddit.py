"""In-memory Reddit modmail provider for local development and tests.

Speaks the real modmail conversation JSON so tests hit the same normalize
path as production. No network.
"""

import hmac
import json
import secrets
from collections.abc import Mapping

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
    split_composite_id,
)
from .reddit import SECRET_HEADER, RedditProvider, parse_conversation


class FakeRedditProvider:
    name = "fake-reddit"
    channel = "reddit"
    capabilities = RedditProvider.capabilities
    connect_credentials = ()
    # zero-config in tests; pass these to exercise the live path
    optional_connect_credentials = ("access_token", "subreddit")

    def __init__(self, webhook_secret: str = "", subreddit: str = "testsub") -> None:
        self.subreddit = subreddit
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._webhook_secret = webhook_secret
        self._n = 0

    def _inbox(self, credentials: Mapping[str, str] | None) -> str:
        sub = (credentials or {}).get("subreddit") or self.subreddit
        return f"r/{sub.lstrip('r/')}"

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        inbox = self._inbox(request.credentials)
        return ProvisionResult(
            address=f"{inbox} (as u/fake_mod)",
            provider_resource_id=inbox,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        conv_id = message.to[0]
        self.sent.append(
            {"inbox": provider_inbox_id, "conversation_id": conv_id, "text": message.text}
        )
        return SendResult(
            provider_message_id=f"{conv_id}:{secrets.randbelow(100000)}",
            provider_thread_id=conv_id,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        conv_id, target = split_composite_id(provider_message_id)
        if not conv_id:
            conv_id = provider_message_id
        self.replies.append(
            {
                "inbox": provider_inbox_id,
                "conversation_id": conv_id,
                "in_reply_to": target,
                "text": message.text,
            }
        )
        return SendResult(
            provider_message_id=f"{conv_id}:{secrets.randbelow(100000)}",
            provider_thread_id=conv_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = (credentials or {}).get("webhook_secret") or self._webhook_secret
        if secret:
            got = lower_headers(headers).get(SECRET_HEADER) or ""
            if not hmac.compare_digest(got, secret):
                raise WebhookVerificationError("secret token mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_conversation(data, self._inbox(credentials))

    def webhook_payload(
        self,
        *,
        conversation_id: str = "abc123",
        message_id: str | None = None,
        text: str = "please unban me",
        subject: str = "ban appeal",
        author: str = "someuser",
        internal: bool = False,
    ) -> dict:
        self._n += 1
        mid = message_id or f"msg{self._n}"
        return {
            "conversation": {
                "id": conversation_id,
                "subject": subject,
                "isInternal": internal,
                "state": 0,
                "numMessages": 1,
                "objIds": [{"id": mid, "key": "messages"}],
                "participant": {
                    "name": author,
                    "id": "t2_user1",
                    "isMod": False,
                    "isAdmin": False,
                    "isOp": True,
                    "isParticipant": True,
                },
                "owner": {
                    "displayName": self.subreddit,
                    "type": "subreddit",
                    "id": "t5_test",
                },
            },
            "messages": {
                mid: {
                    "id": mid,
                    "author": {
                        "name": author,
                        "id": "t2_user1",
                        "isMod": False,
                        "isAdmin": False,
                        "isOp": True,
                        "isParticipant": True,
                    },
                    "body": f"<div>{text}</div>",
                    "bodyMarkdown": text,
                    "date": "2024-06-01T12:00:00.000000+00:00",
                    "participatingAs": "participant",
                }
            },
            "modActions": {},
        }
