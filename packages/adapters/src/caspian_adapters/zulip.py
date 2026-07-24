"""Zulip adapter (official REST API), one bot per connection.

A Zulip **outgoing-webhook bot** drives inbound: the Zulip server POSTs to our
scoped webhook whenever the bot is @-mentioned in a channel or included in a
direct message. Each connection brings its own bot — the realm ``site`` (e.g.
``https://acme.zulipchat.com``), the bot's ``bot_email`` + ``api_key`` for the
REST API, and the outgoing-webhook ``webhook_token`` Zulip echoes in every POST
so we can verify it. The admin registers the webhook URL + token in Zulip when
they create the bot (the one step only a human can do); we do the rest.

Zulip conversations are addressed, not per-message-threaded: a reply is just a
new message sent to the same place. So the routing target travels inside the
composite id rather than a reply-to handle:

- channel message  -> ``stream:{stream_id}:{url-quoted topic}``
- direct message   -> ``dm:{sorted human user ids, comma-separated}``

``provider_message_id`` is that routing prefix plus the Zulip message id
(``…:{id}``); ``provider_thread_id`` is the routing prefix alone. The topic is
URL-quoted because Zulip topics are free-form and may contain ``:`` — quoting
keeps the composite id unambiguous to split. Composite ids never leave this
package.
"""

import hmac
import json
from collections.abc import Mapping
from urllib.parse import quote, unquote

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

CREDENTIAL_FIELDS = ("site", "bot_email", "api_key", "webhook_token")


def encode_stream(stream_id: object, topic: str) -> str:
    """Routing prefix for a channel/topic conversation (topic URL-quoted)."""
    return f"stream:{stream_id}:{quote(topic, safe='')}"


def encode_dm(user_ids: list[int]) -> str:
    """Routing prefix for a direct-message conversation (sorted user ids)."""
    return "dm:" + ",".join(str(i) for i in sorted(user_ids))


def _destination(routing: str) -> tuple[str, dict]:
    """Decode a routing prefix (or a full provider_message_id) into the canonical
    thread id plus the Zulip ``/messages`` fields that address it.

    Accepts either a bare prefix (``stream:2:quoted`` / ``dm:5,9``) or a prefix
    with exactly one trailing message id (``…:12345``) — the trailing id is
    ignored for routing, since Zulip replies target the conversation, not a
    message. Anything with extra segments is malformed and rejected rather than
    silently mis-routed (the topic segment is url-quoted, so it never carries a
    raw ``:`` of its own).
    """
    parts = routing.split(":")
    kind = parts[0]
    # A trailing 4th (stream) / 3rd (dm) segment must be a numeric message id;
    # anything else is a malformed destination, not something to route anyway.
    if kind == "stream" and len(parts) in (3, 4) and (len(parts) == 3 or parts[3].isdigit()):
        stream_id, quoted_topic = parts[1], parts[2]
        # Zulip's `to` accepts a numeric stream id or a channel name; keep whichever
        # we were handed so callers can also address a stream by name.
        to = int(stream_id) if stream_id.isdigit() else stream_id
        fields = {"type": "stream", "to": to, "topic": unquote(quoted_topic)}
        return f"stream:{stream_id}:{quoted_topic}", fields
    if kind == "dm" and len(parts) in (2, 3) and (len(parts) == 2 or parts[2].isdigit()):
        segments = parts[1].split(",")
        if segments and all(s.isdigit() for s in segments):
            ids = [int(s) for s in segments]
            return encode_dm(ids), {"type": "direct", "to": json.dumps(sorted(ids))}
    raise ValueError(f"unroutable Zulip destination: {routing!r}")


def parse_message(data: dict) -> list[InboundMessage]:
    """Normalize a Zulip outgoing-webhook payload into our schema.

    Handles both channel (``type == "stream"``) and direct (``type ==
    "private"``) messages. Empty messages and the bot's own messages are
    dropped, so an agent never talks to itself.
    """
    message = data.get("message")
    if not message:
        return []
    content = message.get("content")
    if not content:
        return []
    bot_email = data.get("bot_email", "")
    if message.get("sender_email") and message["sender_email"] == bot_email:
        return []

    recipients: list[dict] = []
    message_type = message.get("type")
    if message_type == "stream":
        thread = encode_stream(message["stream_id"], message.get("subject", ""))
        chat_type = "channel"
    elif message_type == "private":
        # display_recipient lists every participant including the bot itself; the
        # conversation from the agent's side is the set of *other* humans.
        others = [
            u for u in message.get("display_recipient", []) if u.get("email") != bot_email
        ]
        recipients = [
            {"id": u.get("id"), "email": u.get("email"), "full_name": u.get("full_name")}
            for u in others
        ]
        thread = encode_dm([u["id"] for u in others])
        chat_type = "group" if len(others) > 1 else "private"
    else:
        # Not a message shape we understand (future Zulip types, partial
        # payloads); drop it rather than guess at a DM structure.
        return []

    return [
        InboundMessage(
            external_event_id=str(message["id"]),
            provider_inbox_id=bot_email,
            provider_message_id=f"{thread}:{message['id']}",
            provider_thread_id=thread,
            sender_address=message.get("sender_email"),
            sender_name=message.get("sender_full_name"),
            recipients=recipients,
            text=content,
            chat_type=chat_type,
        )
    ]


class ZulipProvider:
    name = "zulip"
    channel = "zulip"
    connect_credentials = CREDENTIAL_FIELDS
    # An outgoing-webhook bot only receives messages that @-mention it or DM it
    # (no full GROUP_VISIBILITY), can't cold-start a conversation with a stranger
    # (no INITIATE), and has no message history API here (no BACKFILL).
    capabilities = frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND}
    )

    def __init__(
        self,
        site: str = "",
        bot_email: str = "",
        api_key: str = "",
        webhook_token: str = "",
    ) -> None:
        # Deployment-level defaults for a single-tenant install; per-connection
        # credentials override them (see `_creds`).
        self._defaults = {
            "site": site,
            "bot_email": bot_email,
            "api_key": api_key,
            "webhook_token": webhook_token,
        }
        self._client = httpx.Client(timeout=30.0)

    def _creds(self, credentials: Mapping[str, str] | None) -> dict:
        """Per-connection credentials, falling back to deployment defaults."""
        creds = dict(credentials or {})
        for key in CREDENTIAL_FIELDS:
            if not creds.get(key) and self._defaults[key]:
                creds[key] = self._defaults[key]
        return creds

    def _post_message(self, creds: Mapping[str, str], fields: dict, content: str) -> dict:
        missing = [k for k in ("site", "bot_email", "api_key") if not creds.get(k)]
        if missing:
            raise ValueError(f"connection is missing Zulip credentials: {', '.join(missing)}")
        site = creds["site"].rstrip("/")
        response = self._client.post(
            f"{site}/api/v1/messages",
            data={**fields, "content": content},
            auth=(creds["bot_email"], creds["api_key"]),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success":
            raise RuntimeError(f"Zulip send failed: {data.get('msg')}")
        return data

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = self._creds(request.credentials)
        site = creds.get("site", "").rstrip("/")
        resource_id = ""
        if site and creds.get("api_key"):
            response = self._client.get(
                f"{site}/api/v1/users/me",
                auth=(creds["bot_email"], creds["api_key"]),
            )
            response.raise_for_status()
            resource_id = str(response.json().get("user_id", ""))
        return ProvisionResult(
            address=creds.get("bot_email", "zulip"),
            provider_resource_id=resource_id,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        if not message.to:
            raise ValueError("Zulip send requires a routing destination in message.to")
        creds = self._creds(credentials)
        thread, fields = _destination(message.to[0])
        data = self._post_message(creds, fields, message.text or "")
        return SendResult(
            provider_message_id=f"{thread}:{data['id']}", provider_thread_id=thread
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        creds = self._creds(credentials)
        thread, fields = _destination(provider_message_id)
        data = self._post_message(creds, fields, message.text or "")
        return SendResult(
            provider_message_id=f"{thread}:{data['id']}", provider_thread_id=thread
        )

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        if credentials is None:
            # Zulip webhooks are always per-connection; the scoped route supplies
            # the connection's credentials.
            raise WebhookVerificationError("zulip webhooks require a connection scope")
        creds = self._creds(credentials)
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        # Zulip stamps every outgoing-webhook POST with the bot's token, so a
        # connection without one can never verify inbound - fail closed rather
        # than accept unverified payloads from a misconfigured connection.
        expected = creds.get("webhook_token", "")
        if not expected:
            raise WebhookVerificationError("connection is missing a webhook_token credential")
        received = str(data.get("token", ""))
        if not hmac.compare_digest(received, str(expected)):
            raise WebhookVerificationError("Zulip token mismatch")
        return parse_message(data)
