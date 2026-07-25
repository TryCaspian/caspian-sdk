"""In-memory LinkedIn provider for gateway tests."""

import hashlib
import hmac

from ..base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)
from ..linkedin import (
    LinkedInProvider,
    _comment_cursor,
    _cursor_after,
    _encode_poll_cursors,
    _poll_cursors,
    _tracked_posts,
    decode_provider_message_id,
    encode_provider_message_id,
    parse_comments_page,
)


class FakeLinkedInProvider:
    name = "fake-linkedin"
    channel = "linkedin"
    capabilities = LinkedInProvider.capabilities
    connect_credentials = ()
    optional_connect_credentials = LinkedInProvider.optional_connect_credentials

    route_key = staticmethod(LinkedInProvider.route_key)

    def __init__(
        self,
        organization_urn: str = "urn:li:organization:12345",
        tracked_posts: tuple[str, ...] = ("urn:li:ugcPost:70161431162413057",),
        webhook_secret: str = "fake-linkedin-secret",
    ) -> None:
        self.organization_urn = organization_urn
        self.tracked_posts = tracked_posts
        self.webhook_secret = webhook_secret
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self.comments: dict[str, list[dict]] = {post: [] for post in tracked_posts}
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        organization_urn = (request.credentials or {}).get(
            "organization_urn", self.organization_urn
        )
        return ProvisionResult(
            address=f"linkedin:{organization_urn.rsplit(':', 1)[-1]}",
            provider_resource_id=organization_urn,
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        self._seq += 1
        post_urn = f"urn:li:ugcPost:{70161431162413057 + self._seq}"
        self.sent.append({"author": provider_inbox_id, "text": message.text})
        self.comments.setdefault(post_urn, [])
        return SendResult(provider_message_id=post_urn, provider_thread_id=post_urn)

    def reply(
        self,
        provider_inbox_id,
        provider_message_id,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        post_urn, _, comment_urn = decode_provider_message_id(provider_message_id)
        self._seq += 1
        comment_id = str(6643206422739898368 + self._seq)
        new_comment_urn = f"urn:li:comment:(urn:li:activity:6631349431612559360,{comment_id})"
        self.replies.append({
            "post_urn": post_urn,
            "parent_comment": comment_urn,
            "text": message.text,
        })
        return SendResult(
            provider_message_id=encode_provider_message_id(
                post_urn, comment_id, new_comment_urn
            ),
            provider_thread_id=post_urn,
        )

    def poll_comments(self, credentials, cursor: str | None = None):
        organization_urn = (credentials or {}).get("organization_urn", self.organization_urn)
        tracked = _tracked_posts((credentials or {}).get("tracked_posts")) or self.tracked_posts
        cursors = _poll_cursors(cursor, tracked)
        fresh: list[tuple[str, InboundMessage]] = []
        for post_urn in tracked:
            post_cursor = cursors.get(post_urn)
            newest = post_cursor
            for comment in self.comments.get(post_urn, []):
                token = _comment_cursor(comment)
                if _cursor_after(token, newest):
                    newest = token
                if post_cursor is not None and _cursor_after(token, post_cursor):
                    messages = parse_comments_page(
                        {"elements": [comment]},
                        organization_urn,
                        post_urn=post_urn,
                    )
                    fresh.extend((token, message) for message in messages)
            cursors[post_urn] = newest or post_cursor or "0:"
        fresh.sort(key=lambda item: item[0])
        return [message for _, message in fresh], _encode_poll_cursors(cursors)

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        organization_urn = (credentials or {}).get(
            "organization_urn", self.organization_urn
        )
        return LinkedInProvider(webhook_secret=self.webhook_secret).parse_webhook(
            payload,
            headers,
            credentials={"organization_urn": organization_urn},
        )

    def comments_page(self, post_urn: str | None = None, *comments: dict) -> dict:
        target = post_urn or self.tracked_posts[0]
        return {
            "organizationUrn": self.organization_urn,
            "postUrn": target,
            "elements": list(comments) or [self.comment_fixture(object_urn=target)],
        }

    def comment_fixture(
        self,
        *,
        comment_id: str | None = None,
        object_urn: str | None = None,
        actor: str = "urn:li:person:f49f2kf0",
        text: str = "Can someone from support answer this?",
        created_at: int | None = None,
    ) -> dict:
        self._seq += 1
        comment_id = comment_id or str(6636062862760562688 + self._seq)
        object_urn = object_urn or self.tracked_posts[0]
        created_at = created_at or 1_582_160_678_569 + self._seq
        return {
            "actor": actor,
            "commentUrn": (
                f"urn:li:comment:(urn:li:activity:6631349431612559360,{comment_id})"
            ),
            "created": {"actor": actor, "time": created_at},
            "id": comment_id,
            "message": {"attributes": [], "text": text},
            "object": object_urn,
        }

    def signed_headers(self, payload: bytes) -> dict:
        signature = hmac.new(
            self.webhook_secret.encode(),
            b"hmacsha256=" + payload,
            hashlib.sha256,
        ).hexdigest()
        return {"X-LI-Signature": signature}
