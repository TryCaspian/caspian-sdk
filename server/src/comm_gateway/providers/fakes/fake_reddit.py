"""In-memory Reddit provider for local development and tests.

Consumes the real GET /message/inbox listing shape so tests exercise the same
normalization path (parse_inbox) as the live adapter. Zero-config: no
refresh_token needed to exercise send/reply/poll_inbox locally.
"""

import secrets
from collections.abc import Mapping

from ..base import InboundMessage, OutboundMessage, ProvisionRequest, ProvisionResult, SendResult
from ..reddit import RedditProvider, parse_inbox


class FakeRedditProvider:
    name = "fake-reddit"
    channel = "reddit"
    capabilities = RedditProvider.capabilities
    connect_credentials: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._inbox: list[dict] = []  # newest-first, same order as the real API

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"u/fake_{request.agent_id[-6:]}", provider_resource_id="t2_fakeuser",
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        to_user = message.to[0] if message.to else ""
        self.sent.append({"to": to_user, "subject": message.subject, "text": message.text})
        return SendResult(provider_message_id="", provider_thread_id="")

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        self.replies.append({"parent": provider_message_id, "text": message.text})
        new_fullname = f"t1_{secrets.token_hex(4)}"
        return SendResult(provider_message_id=new_fullname, provider_thread_id=provider_message_id)

    def poll_inbox(
        self, credentials: Mapping[str, str] | None = None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str | None]:
        newest = self._inbox[0]["data"]["id"] if self._inbox else None
        newest_fullname = f"t4_{newest}" if newest else cursor
        if cursor is None:
            return [], newest_fullname
        data = {"data": {"children": self._inbox}}
        fresh = parse_inbox(data, cursor)
        return fresh, newest_fullname or cursor

    def deliver_message(
        self, *, author: str = "customer", subject: str = "Question", body: str = "Hi there",
        dest: str = "fake_bot",
    ) -> str:
        """Push a private message into the fake inbox (newest-first), as if a
        real user had just sent one. Returns the new message's fullname."""
        msg_id = secrets.token_hex(4)
        self._inbox.insert(
            0,
            {
                "kind": "t4",
                "data": {
                    "id": msg_id, "author": author, "subject": subject,
                    "body": body, "dest": dest,
                },
            },
        )
        return f"t4_{msg_id}"

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str], credentials=None):
        from ..base import WebhookVerificationError

        raise WebhookVerificationError("reddit has no webhook delivery; use poll_inbox() instead")
