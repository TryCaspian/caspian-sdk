"""Reddit modmail adapter (official OAuth API), one subreddit per connection.

Inbox/chat DMs are mostly poll-based, so this uses new modmail instead - it
fits parse_webhook the same way Telegram updates do. Caller brings an OAuth
token with the modmail scope plus the subreddit they moderate.

- provision hits /api/v1/me; address looks like r/<sub> (as u/<name>)
- inbound payload is the same shape as GET /api/mod/conversations/:id
  (gateway/Devvit can forward it). Verified with a per-connection secret in
  X-Caspian-Webhook-Secret - Reddit doesn't sign this push path itself.
- provider_thread_id = conversation id
- provider_message_id = "{conversation_id}:{message_id}" for replies
- send/reply -> POST /api/mod/conversations/:id

No INITIATE - we only talk in threads that already exist.
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
    split_composite_id,
)

API = "https://oauth.reddit.com"
SECRET_HEADER = "x-caspian-webhook-secret"
# reddit 429s / rejects bare default UAs
USER_AGENT = "caspian-adapters/reddit by trycaspian"


def parse_conversation(data: dict, inbox_id: str) -> list[InboundMessage]:
    """Normalize a modmail conversation into our schema. Skips internal notes."""
    conv = data.get("conversation") or {}
    conv_id = conv.get("id")
    if not conv_id or conv.get("isInternal"):
        return []

    raw = data.get("messages") or {}
    if isinstance(raw, dict):
        # objIds is reddit's order; fall back to map insertion order
        ids = [
            o["id"]
            for o in (conv.get("objIds") or [])
            if o.get("key") == "messages" and o.get("id") in raw
        ] or list(raw.keys())
        msgs = [raw[i] for i in ids if i in raw]
    elif isinstance(raw, list):
        msgs = raw
    else:
        msgs = []

    # some forwarders send a single top-level "message"
    if not msgs and isinstance(data.get("message"), dict):
        msgs = [data["message"]]

    last = None
    for m in reversed(msgs):
        if (m.get("bodyMarkdown") or m.get("body") or "").strip():
            last = m
            break
    if last is None:
        return []

    text = (last.get("bodyMarkdown") or last.get("body") or "").strip()
    author = last.get("author") or conv.get("participant") or {}
    mid = str(last.get("id") or "")
    composite = f"{conv_id}:{mid}" if mid else str(conv_id)

    return [
        InboundMessage(
            external_event_id=composite,
            provider_inbox_id=inbox_id,
            provider_message_id=composite,
            provider_thread_id=str(conv_id),
            sender_address=author.get("name") or author.get("id"),
            sender_name=author.get("name"),
            subject=conv.get("subject"),
            text=text,
            chat_type="modmail",
        )
    ]


class RedditProvider:
    name = "reddit"
    channel = "reddit"
    connect_credentials = ("access_token", "subreddit")
    capabilities = frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND}
    )

    def __init__(
        self,
        base_url: str = API,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._user_agent = user_agent
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)

    @staticmethod
    def _require(credentials: Mapping[str, str] | None) -> dict[str, str]:
        creds = dict(credentials or {})
        token = creds.get("access_token", "")
        sub = (creds.get("subreddit") or "").lstrip("r/").strip()
        if not token:
            raise ValueError("connection is missing access_token")
        if not sub:
            raise ValueError("connection is missing subreddit")
        creds["access_token"] = token
        creds["subreddit"] = sub
        return creds

    def _auth(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"bearer {token}",
            "User-Agent": self._user_agent,
        }

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = self._require(request.credentials)
        r = self._client.get("/api/v1/me", headers=self._auth(creds["access_token"]))
        r.raise_for_status()
        me = r.json()
        sub = creds["subreddit"]
        return ProvisionResult(
            address=f"r/{sub} (as u/{me.get('name') or 'reddit'})",
            provider_resource_id=f"r/{sub}",
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        creds = self._require(credentials)
        if not message.to:
            raise ValueError("reddit send needs message.to[0] = conversation id")
        return self._reply_to(creds, message.to[0], message.text or "")

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        creds = self._require(credentials)
        conv_id, _ = split_composite_id(provider_message_id)
        return self._reply_to(creds, conv_id or provider_message_id, message.text or "")

    def _reply_to(self, creds: dict[str, str], conv_id: str, body: str) -> SendResult:
        r = self._client.post(
            f"/api/mod/conversations/{conv_id}",
            headers=self._auth(creds["access_token"]),
            data={"body": body},
        )
        r.raise_for_status()
        messages = r.json().get("messages") or {}
        new_id = ""
        if isinstance(messages, dict) and messages:
            new_id = str(next(reversed(list(messages.keys()))))
        return SendResult(
            provider_message_id=f"{conv_id}:{new_id}" if new_id else conv_id,
            provider_thread_id=conv_id,
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        if credentials is None:
            # same idea as telegram - webhook is always scoped to a connection
            raise WebhookVerificationError("reddit webhooks require a connection scope")
        secret = credentials.get("webhook_secret")
        if secret:
            got = lower_headers(headers).get(SECRET_HEADER) or ""
            if not hmac.compare_digest(got, secret):
                raise WebhookVerificationError("secret token mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        try:
            creds = self._require(credentials)
        except ValueError as exc:
            raise WebhookVerificationError(str(exc)) from exc
        return parse_conversation(data, f"r/{creds['subreddit']}")
