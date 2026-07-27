"""In-memory Reddit provider using real /message/unread listing shapes."""

from ..base import OutboundMessage, ProvisionRequest, ProvisionResult, SendResult
from ..reddit import RedditProvider, _item_to_inbound


class FakeRedditProvider:
    name = "fake-reddit"
    channel = "reddit"
    capabilities = RedditProvider.capabilities
    connect_credentials = ()
    optional_connect_credentials = (
        "client_id",
        "client_secret",
        "refresh_token",
        "user_agent",
    )

    def __init__(self) -> None:
        self.username = "fake_agent"
        self.user_id = "fake123"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self.unread: list[dict] = []
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = request.credentials or {}
        username = credentials.get("username", self.username)
        return ProvisionResult(
            address=f"u/{username}",
            provider_resource_id=f"t2_{self.user_id}",
        )

    def _next_fullname(self, kind: str) -> str:
        self._seq += 1
        return f"{kind}_fake{self._seq}"

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        del provider_inbox_id, credentials
        if not message.to:
            raise ValueError("reddit send requires an existing thing fullname in message.to[0]")
        new_fullname = self._next_fullname("t1")
        self.sent.append({"thing_id": message.to[0], "text": message.text, "name": new_fullname})
        return SendResult(provider_message_id=new_fullname, provider_thread_id=message.to[0])

    def reply(
        self, provider_inbox_id, provider_message_id, message: OutboundMessage, credentials=None
    ) -> SendResult:
        del provider_inbox_id, credentials
        new_fullname = self._next_fullname("t1")
        self.replies.append(
            {"thing_id": provider_message_id, "text": message.text, "name": new_fullname}
        )
        return SendResult(provider_message_id=new_fullname, provider_thread_id=provider_message_id)

    def push_message(self, *, author: str, body: str, subject: str = "test") -> dict:
        """Test helper: enqueue a private message as Reddit's API would shape
        it in a /message/unread listing child."""
        item = {
            "name": self._next_fullname("t4"),
            "author": author,
            "body": body,
            "subject": subject,
            "created_utc": float(len(self.unread) + 1),
        }
        self.unread.append(item)
        return item

    def poll_inbox(self, credentials, cursor: str | None = None):
        provider_inbox_id = f"t2_{self.user_id}"
        timestamps = [str(item["created_utc"]) for item in self.unread]
        newest = max(timestamps + ([cursor] if cursor else []), default=cursor, key=float) \
            if (timestamps or cursor) else cursor

        if cursor is None:
            return [], newest or "0"

        fresh = [item for item in self.unread if float(item["created_utc"]) > float(cursor)]
        fresh.sort(key=lambda i: float(i["created_utc"]))
        messages = [
            m for c in fresh if (m := _item_to_inbound(c, provider_inbox_id)) is not None
        ]
        return messages, newest or cursor
