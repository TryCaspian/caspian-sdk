"""Reddit adapter: reactive private messages via OAuth2 (script/installed app).

Reddit has no webhook for private messages, unlike Slack/Discord/Telegram, so
inbound follows the same polling pattern as x.py's poll_dms: the caller polls
GET /message/inbox on an interval, we return only messages newer than the
cursor, oldest-first.

Auth: OAuth2 with a long-lived refresh_token (minted once via Reddit's
"installed app" or "script app" flow -- a human authorizes it, we never see a
password). We exchange it for a short-lived access token on demand and cache
it until it expires.

Send: POST /api/compose (to, subject, text) starts a new PM thread -- this is
the one channel here where a first-touch DM is allowed by the platform itself
(compose has no "must have messaged first" restriction), so INITIATE is a
real capability, unlike X.

Reply: Reddit unifies comments and PM replies under POST /api/comment, keyed
by the parent's fullname (t4_xxxxx for a private message). There is no
separate "reply to PM" endpoint.

provider_message_id is the message's Reddit fullname (e.g. "t4_1abcde"),
so reply() can pass it straight through as `parent` with no lookup.
provider_thread_id is the same fullname for a fresh message -- Reddit PMs
don't have a separate thread id from the message that started them.
"""

import time
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
)


def parse_inbox(data: dict, since_fullname: str | None) -> list[InboundMessage]:
    """Turn a GET /message/inbox listing into InboundMessages.

    Keeps only private messages (kind "t4"); comment replies and mentions
    (kind "t1") show up in the same inbox listing but are a different shape
    and out of scope for a DM channel. Skips anything at or before the
    cursor so a restart never re-delivers messages already seen.
    """
    out: list[InboundMessage] = []
    for child in data.get("data", {}).get("children", []):
        if child.get("kind") != "t4":
            continue
        item = child.get("data", {})
        fullname = f"t4_{item['id']}"
        if since_fullname and fullname == since_fullname:
            break  # inbox is newest-first; everything after this was already seen
        out.append(
            InboundMessage(
                external_event_id=fullname,
                provider_inbox_id=item.get("dest", ""),
                provider_message_id=fullname,
                provider_thread_id=fullname,
                sender_address=item.get("author"),
                sender_name=item.get("author"),
                subject=item.get("subject"),
                text=item.get("body"),
                chat_type="reddit_dm",
            )
        )
    out.reverse()  # oldest-first, matching the poll_dms convention in x.py
    return out


class RedditProvider:
    name = "reddit"
    channel = "reddit"
    # Unlike X, Reddit's compose endpoint allows a cold first message (no prior
    # contact required), so INITIATE is real here, not banned by platform ToS.
    capabilities = frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND, Capability.INITIATE}
    )
    # Per-connection: the refresh token minted when this specific Reddit
    # account authorized the app (never a password).
    connect_credentials: tuple[str, ...] = ("refresh_token",)

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        user_agent: str = "caspian-sdk/0.1 (by /u/caspian-bot)",
        base_url: str = "https://oauth.reddit.com",
        auth_url: str = "https://www.reddit.com",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._base_url = base_url.rstrip("/")
        self._auth_url = auth_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url, timeout=30.0, headers={"User-Agent": user_agent}
        )
        # Separate client for the token endpoint, which lives on a different
        # host (www.reddit.com, not oauth.reddit.com) -- kept as its own
        # attribute so tests can mock it independently of the API client.
        self._auth_client = httpx.Client(
            base_url=self._auth_url, timeout=30.0, headers={"User-Agent": user_agent}
        )
        # Cache of access tokens per refresh_token, so a burst of calls for the
        # same connection doesn't re-authenticate on every request.
        self._token_cache: dict[str, tuple[str, float]] = {}

    def _access_token(self, credentials: Mapping[str, str] | None) -> str:
        refresh_token = (credentials or {}).get("refresh_token", "")
        if not refresh_token:
            raise ValueError("connection is missing a refresh_token credential")
        cached = self._token_cache.get(refresh_token)
        if cached and cached[1] > time.time():
            return cached[0]
        response = self._auth_client.post(
            "/api/v1/access_token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(self._client_id, self._client_secret),
        )
        response.raise_for_status()
        payload = response.json()
        token = payload["access_token"]
        # Refresh a little early so a request never races an expiring token.
        expires_at = time.time() + payload.get("expires_in", 3600) - 60
        self._token_cache[refresh_token] = (token, expires_at)
        return token

    def _auth_headers(self, credentials: Mapping[str, str] | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token(credentials)}"}

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        headers = self._auth_headers(request.credentials)
        response = self._client.get("/api/v1/me", headers=headers)
        response.raise_for_status()
        me = response.json()
        return ProvisionResult(
            address=f"u/{me['name']}", provider_resource_id=me["id"],
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        to_user = message.to[0] if message.to else ""
        response = self._client.post(
            "/api/compose",
            headers=self._auth_headers(credentials),
            data={
                "to": to_user,
                "subject": message.subject or "Message",
                "text": message.text or "",
                "api_type": "json",
            },
        )
        response.raise_for_status()
        # Reddit's compose response doesn't hand back the new message's fullname
        # directly, so callers should treat provider_message_id as best-effort
        # here and rely on the next inbox poll for a durable id to reply against.
        return SendResult(provider_message_id="", provider_thread_id="")

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        response = self._client.post(
            "/api/comment",
            headers=self._auth_headers(credentials),
            data={
                "thing_id": provider_message_id,
                "text": message.text or "",
                "api_type": "json",
            },
        )
        response.raise_for_status()
        data = response.json()["json"]["data"]["things"][0]["data"]
        new_fullname = f"t1_{data['id']}"
        return SendResult(
            provider_message_id=new_fullname, provider_thread_id=provider_message_id,
        )

    def poll_inbox(
        self, credentials: Mapping[str, str] | None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str | None]:
        """Poll GET /message/inbox for new private messages (the no-webhook path).

        Returns (new_messages, new_cursor). `cursor` is the newest message
        fullname seen last time. On the first poll (cursor is None) we adopt
        the newest fullname as a baseline and return nothing, so the agent
        never replies to the whole PM history it inherits on first connect --
        only to messages that arrive afterward. Mirrors x.py's poll_dms.
        """
        response = self._client.get(
            "/message/inbox", headers=self._auth_headers(credentials), params={"limit": 50},
        )
        response.raise_for_status()
        data = response.json()
        children = data.get("data", {}).get("children", [])
        newest = children[0]["data"]["id"] if children else None
        newest_fullname = f"t4_{newest}" if newest else cursor
        if cursor is None:
            return [], newest_fullname
        fresh = parse_inbox(data, cursor)
        return fresh, newest_fullname or cursor

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        # Reddit has no webhook delivery for private messages -- inbound only
        # arrives via poll_inbox(). This exists to satisfy the provider
        # interface, not to be wired to a real route.
        raise WebhookVerificationError(
            "reddit has no webhook delivery for private messages; use poll_inbox() instead"
        )
