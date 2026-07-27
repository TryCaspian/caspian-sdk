"""Reddit adapter using the official OAuth2 API (private messages).

Reddit has no push-webhook mechanism for private messages, so inbound works
the same way it does for Bluesky and X: a background poller calls
``poll_inbox`` on an interval per connection, diffs against a stored cursor,
and ingests only what's new. ``parse_webhook`` exists for parity with every
other provider and for callers that already have a Reddit listing payload in
hand (e.g. a manual replay); it verifies a shared secret header the same way
Bluesky's does, since Reddit itself never calls it.

Each connected account authenticates as its own OAuth2 app (client_id +
client_secret + a long-lived refresh_token) - never a shared deployment
credential - so one Caspian deployment can host many Reddit accounts without
their tokens colliding. Reddit requires a descriptive User-Agent on every
request; we always send one.

REACTIVE ONLY, same posture as the X adapter: this provider never cold-starts
a conversation with a stranger (no Capability.INITIATE - composing an
unsolicited top-level PM is exactly the unsolicited-outreach pattern
CONTRIBUTING.md rules out). ``send`` and ``reply`` both post into a thread
that already exists - ``send`` is keyed by a known fullname the caller
supplies in ``message.to[0]`` (e.g. continuing a message thread or commenting
on a post), ``reply`` is keyed by the ``provider_message_id`` of the inbound
item being answered. Reddit's own API treats these identically: replying to
a private message and replying to a comment are both POST /api/comment with
a ``thing_id``.

Message identifiers: Reddit's own "fullname" (``t4_xxx`` for a private
message, ``t1_xxx`` for a comment reply notification) is already a stable,
globally unique id, so it is used directly as both ``external_event_id`` and
``provider_message_id`` - no composite encoding needed.
"""

import hmac
import json
from collections.abc import Mapping

import httpx

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)

TOKEN_PATH = "/api/v1/access_token"
ME_PATH = "/api/v1/me"
UNREAD_PATH = "/message/unread"
COMMENT_PATH = "/api/comment"

TOKEN_HEADER = "x-caspian-webhook-token"

DEFAULT_USER_AGENT = "caspian-sdk/1.0 (channel adapter; https://trycaspianai.com)"

MISSING_CREDENTIALS_ERROR = (
    "reddit requires client_id, client_secret, and refresh_token in the "
    "connection credentials"
)
MISSING_ACCESS_TOKEN_ERROR = "reddit access_token response is missing access_token"
MISSING_USERNAME_ERROR = "reddit /api/v1/me response is missing a username"
MISSING_TO_ERROR = "reddit send requires an existing thing fullname in message.to[0]"
MISSING_TEXT_ERROR = "reddit requires a text message"
INVALID_WEBHOOK_PAYLOAD_ERROR = "invalid Reddit webhook payload"
WEBHOOK_TOKEN_MISMATCH_ERROR = "Reddit webhook token mismatch"
MISSING_WEBHOOK_SECRET_ERROR = "Reddit webhook secret is not configured"


def _require_credentials(credentials: Mapping[str, str] | None) -> dict[str, str]:
    creds = dict(credentials or {})
    if not creds.get("client_id") or not creds.get("client_secret") or not creds.get(
        "refresh_token"
    ):
        raise ValueError(MISSING_CREDENTIALS_ERROR)
    return creds


def _item_kind_and_id(item: Mapping[str, object]) -> tuple[str, str]:
    """Split a listing child's fullname (e.g. "t4_abc123") into kind + id36."""
    fullname = str(item.get("name") or "")
    kind, _, tail = fullname.partition("_")
    return kind, tail


def _thread_id(item: Mapping[str, object], fullname: str) -> str:
    """Best-effort thread id: a message's root ("first_message_name"), a
    comment's parent link ("link_id"), or the item itself when neither applies
    (a top-level private message that started its own thread)."""
    root = item.get("first_message_name")
    if isinstance(root, str) and root:
        return root
    link_id = item.get("link_id")
    if isinstance(link_id, str) and link_id:
        return link_id
    return fullname


def _item_to_inbound(item: Mapping[str, object], provider_inbox_id: str) -> InboundMessage | None:
    """Normalize one Reddit /message/unread listing child into InboundMessage.

    Handles both kinds Reddit returns in the inbox: "t4" private messages and
    "t1" comment replies. Anything else (e.g. a future kind we don't
    recognize) is skipped rather than guessed at.
    """
    kind, item_id = _item_kind_and_id(item)
    if kind not in ("t1", "t4") or not item_id:
        return None

    fullname = str(item.get("name"))
    text = item.get("body")
    if not isinstance(text, str) or not text:
        return None

    author = item.get("author")
    sender = author if isinstance(author, str) and author else None

    return InboundMessage(
        external_event_id=fullname,
        provider_inbox_id=provider_inbox_id,
        provider_message_id=fullname,
        provider_thread_id=_thread_id(item, fullname),
        sender_address=sender,
        sender_name=sender,
        subject=item.get("subject") if kind == "t4" else None,
        text=text,
        chat_type="private" if kind == "t4" else "public",
    )


def _children_from_listing(payload: object, *, error_message: str) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError(error_message)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(error_message)
    children = data.get("children")
    if not isinstance(children, list):
        raise ValueError(error_message)
    out = []
    for child in children:
        if isinstance(child, dict) and isinstance(child.get("data"), dict):
            out.append(child["data"])
    return out


class RedditProvider:
    """Caspian channel provider for a Reddit account's private messages."""

    name = "reddit"
    channel = "reddit"

    # Reactive only - see module docstring. No INITIATE: this adapter never
    # composes an unsolicited first message to a stranger.
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    connect_credentials: tuple[str, ...] = (
        "client_id",
        "client_secret",
        "refresh_token",
    )
    optional_connect_credentials: tuple[str, ...] = ("user_agent",)

    def __init__(
        self,
        base_url: str = "https://oauth.reddit.com",
        token_url: str = "https://www.reddit.com",
        webhook_secret: str = "",
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._default_user_agent = user_agent or DEFAULT_USER_AGENT
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)
        self._token_client = httpx.Client(base_url=token_url.rstrip("/"), timeout=30.0)

    def _user_agent(self, credentials: Mapping[str, str]) -> str:
        return credentials.get("user_agent") or self._default_user_agent

    def _access_token(self, credentials: Mapping[str, str]) -> str:
        """Exchange the connection's refresh_token for a short-lived access
        token. Reddit access tokens are not cached here (they last ~1 hour and
        this call is infrequent - once per poll/action), keeping the adapter
        stateless the same way the other OAuth providers' per-call token
        fetches do."""
        response = self._token_client.post(
            TOKEN_PATH,
            auth=(credentials["client_id"], credentials["client_secret"]),
            data={
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
            },
            headers={"User-Agent": self._user_agent(credentials)},
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError(MISSING_ACCESS_TOKEN_ERROR)
        return token

    def _headers(self, credentials: Mapping[str, str]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token(credentials)}",
            "User-Agent": self._user_agent(credentials),
        }

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = _require_credentials(request.credentials)
        response = self._client.get(ME_PATH, headers=self._headers(credentials))
        response.raise_for_status()
        me = response.json()
        username = me.get("name") if isinstance(me, dict) else None
        if not isinstance(username, str) or not username:
            raise ValueError(MISSING_USERNAME_ERROR)
        user_id = me.get("id") if isinstance(me, dict) else None
        resource_id = f"t2_{user_id}" if user_id else username
        return ProvisionResult(address=f"u/{username}", provider_resource_id=resource_id)

    def _post_comment(
        self, thing_id: str, message: OutboundMessage, credentials: Mapping[str, str] | None
    ) -> SendResult:
        if not message.text:
            raise ValueError(MISSING_TEXT_ERROR)
        creds = _require_credentials(credentials)
        response = self._client.post(
            COMMENT_PATH,
            headers=self._headers(creds),
            data={"api_type": "json", "thing_id": thing_id, "text": message.text},
        )
        response.raise_for_status()
        body = response.json()
        things = (
            body.get("json", {}).get("data", {}).get("things", [])
            if isinstance(body, dict)
            else []
        )
        new_fullname = thing_id
        if things and isinstance(things[0], dict):
            new_data = things[0].get("data", {})
            candidate = new_data.get("name") if isinstance(new_data, dict) else None
            if isinstance(candidate, str) and candidate:
                new_fullname = candidate
        return SendResult(provider_message_id=new_fullname, provider_thread_id=thing_id)

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        del provider_inbox_id
        if not message.to or not message.to[0]:
            raise ValueError(MISSING_TO_ERROR)
        return self._post_comment(message.to[0], message, credentials)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        del provider_inbox_id
        return self._post_comment(provider_message_id, message, credentials)

    def poll_inbox(
        self,
        credentials: Mapping[str, str] | None,
        cursor: str | None = None,
    ) -> tuple[list[InboundMessage], str]:
        """Poll unread private messages / comment replies.

        Cursor is the newest `created_utc` (as a string) seen last time. On
        the first poll (cursor is None) we adopt the newest timestamp as a
        baseline and return nothing, so a newly connected agent never replies
        to a backlog it inherited - only to what arrives after it comes
        online, matching the X and Bluesky pollers.
        """
        creds = _require_credentials(credentials)
        response = self._client.get(UNREAD_PATH, headers=self._headers(creds))
        response.raise_for_status()
        children = _children_from_listing(
            response.json(), error_message="invalid Reddit /message/unread response"
        )

        timestamps = [
            str(child["created_utc"])
            for child in children
            if isinstance(child.get("created_utc"), (int, float))
        ]
        newest = max(timestamps + ([cursor] if cursor else []), default=cursor, key=float) \
            if (timestamps or cursor) else cursor

        if cursor is None:
            return [], newest or "0"

        fresh = [
            child
            for child in children
            if isinstance(child.get("created_utc"), (int, float))
            and float(child["created_utc"]) > float(cursor)
        ]
        fresh.sort(key=lambda c: float(c["created_utc"]))

        provider_inbox_id = creds.get("provider_resource_id", "")
        messages = [m for c in fresh if (m := _item_to_inbound(c, provider_inbox_id)) is not None]
        return messages, newest or cursor

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        """Verify and normalize an out-of-band Reddit listing payload.

        Reddit has no push webhooks for messages; this exists for parity with
        every other provider (and for callers replaying a captured listing).
        Real inbound arrives through the poller (see listeners/manager.py),
        which calls poll_inbox directly.
        """
        if not self._webhook_secret:
            raise WebhookVerificationError(MISSING_WEBHOOK_SECRET_ERROR)

        received = lower_headers(headers).get(TOKEN_HEADER, "").encode("utf-8")
        expected = self._webhook_secret.encode("utf-8")
        if not hmac.compare_digest(received, expected):
            raise WebhookVerificationError(WEBHOOK_TOKEN_MISMATCH_ERROR)

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR) from exc

        try:
            children = _children_from_listing(data, error_message=INVALID_WEBHOOK_PAYLOAD_ERROR)
        except ValueError as exc:
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR) from exc

        provider_inbox_id = (credentials or {}).get("provider_resource_id", "")
        return [m for c in children if (m := _item_to_inbound(c, provider_inbox_id)) is not None]
