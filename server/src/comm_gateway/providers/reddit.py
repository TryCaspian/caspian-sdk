"""Reddit adapter using Reddit's official OAuth2 API.

Each connected account supplies its own Reddit developer app (script-type)
credentials plus the bot account's Reddit username/password. The adapter
authenticates through the OAuth2 "password" grant
(https://www.reddit.com/api/v1/access_token), sends and replies to private
messages through ``/api/compose`` and ``/api/comment``, and receives new
messages by polling ``/message/unread`` -- Reddit's official inbox surface.

This adapter only automates the official REST API. It never scrapes or
drives an unofficial protocol, and it never touches subreddit moderation
surfaces (modmail, removals, bans) -- private messages only.

Reddit has no native push-webhook system for private messages, so live
inbound arrives through ``poll_messages()`` (see the listener manager),
the same shape as the Bluesky adapter's ``poll_notifications()``.
``parse_webhook`` exists for testing symmetry and any self-hosted relay
that wants to forward a poll result through the shared webhook pipeline; it
verifies a shared-secret header the same way the Bluesky webhook path does.

Provider message identifiers are Reddit's own "fullname" strings
(``t4_...`` for a message, ``t1_...`` for a comment-kind reply) -- Reddit
already hands back a single opaque, stable id per object, so no extra
encoding is needed the way Bluesky's multi-field AT URIs require.
"""

import hmac
import json
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
    lower_headers,
)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
DEFAULT_BASE_URL = "https://oauth.reddit.com"

ME_PATH = "/api/v1/me"
COMPOSE_PATH = "/api/compose"
COMMENT_PATH = "/api/comment"
UNREAD_PATH = "/message/unread"
READ_MESSAGE_PATH = "/api/read_message"

# Reddit's API rules require an informative, non-generic User-Agent
# identifying the app and its purpose; a default here keeps the adapter
# usable out of the box, but deployments should set their own via
# COMM_REDDIT_USER_AGENT once they have their own app name / contact.
DEFAULT_USER_AGENT = "caspian-sdk-reddit-adapter/1.0 (by /u/caspian-agent)"

TOKEN_HEADER = "x-caspian-webhook-token"

MISSING_CREDENTIALS_ERROR = (
    "reddit requires client_id, client_secret, username, and password in the "
    "connection credentials"
)
TOKEN_AUTH_ERROR = "Reddit authentication failed"
INVALID_TOKEN_RESPONSE_ERROR = "reddit access_token endpoint returned an invalid response"
MISSING_ACCESS_TOKEN_ERROR = "reddit access_token response is missing access_token"

INVALID_ME_RESPONSE_ERROR = "reddit /api/v1/me returned an invalid response"
MISSING_USERNAME_ERROR = "reddit /api/v1/me response is missing name"
MISSING_ID_ERROR = "reddit /api/v1/me response is missing id"

MISSING_TEXT_ERROR = "reddit requires a text message"
MISSING_RECIPIENT_ERROR = "reddit send requires exactly one recipient username in `to`"
COMPOSE_ERROR = "reddit /api/compose returned an error"
COMMENT_ERROR = "reddit /api/comment returned an error"
MISSING_COMMENT_NAME_ERROR = "reddit /api/comment response is missing the reply's fullname"
MISSING_PARENT_ID_ERROR = "reddit reply requires a provider_message_id"

INVALID_WEBHOOK_PAYLOAD_ERROR = "invalid Reddit webhook payload"
WEBHOOK_TOKEN_MISMATCH_ERROR = "Reddit webhook token mismatch"
MISSING_WEBHOOK_INBOX_ERROR = "Reddit webhook requires a provider inbox id"
MISSING_WEBHOOK_SECRET_ERROR = "Reddit webhook secret is not configured"


def _require_string(data: Mapping[str, object], key: str, error_message: str) -> str:
    """Return a required non-empty string value."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(error_message)
    return value


def _json_errors(result: object) -> list:
    """Extract Reddit's `json.errors` array from an api_type=json response."""
    if not isinstance(result, dict):
        return []
    body = result.get("json")
    if not isinstance(body, dict):
        return []
    errors = body.get("errors")
    return errors if isinstance(errors, list) else []


def _created_utc(entry: Mapping[str, object]) -> float | None:
    """Extract a Reddit inbox entry's created_utc as a float, if present."""
    data = entry.get("data")
    if not isinstance(data, dict):
        return None
    value = data.get("created_utc")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


class RedditProvider:
    """Caspian channel provider for a Reddit account's private messages."""

    name = "reddit"
    channel = "reddit"

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
        "username",
        "password",
    )
    optional_connect_credentials: tuple[str, ...] = ()

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token_url: str = TOKEN_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        webhook_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_url = token_url
        self._user_agent = user_agent
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(base_url=self._base_url, timeout=30.0)
        self._token_client = httpx.Client(timeout=30.0)

    # --- auth -----------------------------------------------------------

    def _authenticate(self, credentials: Mapping[str, str] | None) -> str:
        """Exchange script-app + account credentials for a bearer token.

        Reddit's OAuth2 "password" grant is the standard shape for a script
        app acting as a single Reddit account -- the same per-developer,
        per-account credential model the Bluesky and Twilio adapters use.
        """
        creds = credentials or {}
        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")
        username = creds.get("username")
        password = creds.get("password")

        if not (client_id and client_secret and username and password):
            raise ValueError(MISSING_CREDENTIALS_ERROR)

        response = self._token_client.post(
            self._token_url,
            auth=(client_id, client_secret),
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            headers={"User-Agent": self._user_agent},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ValueError(TOKEN_AUTH_ERROR) from exc

        token = response.json()
        if not isinstance(token, dict):
            raise ValueError(INVALID_TOKEN_RESPONSE_ERROR)

        return _require_string(token, "access_token", MISSING_ACCESS_TOKEN_ERROR)

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self._user_agent,
        }

    def _me(self, access_token: str) -> dict[str, object]:
        response = self._client.get(ME_PATH, headers=self._headers(access_token))
        response.raise_for_status()
        me = response.json()
        if not isinstance(me, dict):
            raise ValueError(INVALID_ME_RESPONSE_ERROR)
        return me

    # --- ChannelProvider contract ----------------------------------------

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        """Validate credentials and provision the connected Reddit account."""
        access_token = self._authenticate(request.credentials)
        me = self._me(access_token)

        username = _require_string(me, "name", MISSING_USERNAME_ERROR)
        account_id = _require_string(me, "id", MISSING_ID_ERROR)

        return ProvisionResult(
            address=username,
            provider_resource_id=f"t2_{account_id}",
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        """Compose a new private message to a Reddit username.

        Reddit's /api/compose response never echoes the new message's
        fullname (there is no id to hand back -- Reddit's own inbox UI has
        the identical limitation), so the thread is addressed by recipient
        username until the human replies and we receive a real fullname to
        continue from.
        """
        del provider_inbox_id

        if not message.text:
            raise ValueError(MISSING_TEXT_ERROR)
        if len(message.to) != 1 or not message.to[0]:
            raise ValueError(MISSING_RECIPIENT_ERROR)

        access_token = self._authenticate(credentials)
        to = message.to[0]

        response = self._client.post(
            COMPOSE_PATH,
            headers=self._headers(access_token),
            data={
                "api_type": "json",
                "to": to,
                "subject": message.subject or "Message from your agent",
                "text": message.text,
            },
        )
        response.raise_for_status()
        result = response.json()
        errors = _json_errors(result)
        if errors:
            raise ValueError(f"{COMPOSE_ERROR}: {errors}")

        placeholder = f"pending:{to}"
        return SendResult(
            provider_message_id=placeholder,
            provider_thread_id=placeholder,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        """Reply to an inbound private message via Reddit's comment endpoint.

        Reddit answers a private message the same way it answers a comment:
        POST /api/comment with the parent's fullname as thing_id. The
        response is a comment-kind (t1_...) object; its fullname becomes the
        new provider_message_id so a further reply threads off it in turn.
        """
        del provider_inbox_id

        if not message.text:
            raise ValueError(MISSING_TEXT_ERROR)
        if not provider_message_id:
            raise ValueError(MISSING_PARENT_ID_ERROR)

        access_token = self._authenticate(credentials)

        response = self._client.post(
            COMMENT_PATH,
            headers=self._headers(access_token),
            data={
                "api_type": "json",
                "thing_id": provider_message_id,
                "text": message.text,
            },
        )
        response.raise_for_status()
        result = response.json()
        errors = _json_errors(result)
        if errors:
            raise ValueError(f"{COMMENT_ERROR}: {errors}")

        things = result.get("json", {}).get("data", {}).get("things", [])
        if not isinstance(things, list) or not things or not isinstance(things[0], dict):
            raise ValueError(MISSING_COMMENT_NAME_ERROR)

        name = things[0].get("data", {}).get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(MISSING_COMMENT_NAME_ERROR)

        return SendResult(
            provider_message_id=name,
            provider_thread_id=name,
        )

    # --- receiving (polling; see listeners/manager.py) --------------------

    def _messages_from_listing(self, payload: object) -> list[dict[str, object]]:
        """Extract message entries from a Reddit Listing payload."""
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        children = data.get("children")
        if not isinstance(children, list):
            return []
        return [child for child in children if isinstance(child, dict)]

    def _newest_cursor(
        self,
        entries: list[dict[str, object]],
        current_cursor: str | None,
    ) -> str | None:
        """Return the newest created_utc seen, as a stable string cursor."""
        values = [ts for entry in entries if (ts := _created_utc(entry)) is not None]
        if current_cursor is not None:
            try:
                values.append(float(current_cursor))
            except ValueError:
                pass
        if not values:
            return current_cursor
        return repr(max(values))

    def _fresh_messages(
        self,
        entries: list[dict[str, object]],
        cursor: str,
    ) -> list[dict[str, object]]:
        """Return entries newer than the stored cursor, oldest first."""
        try:
            boundary = float(cursor)
        except ValueError:
            boundary = None

        fresh = []
        for entry in entries:
            created = _created_utc(entry)
            if created is None:
                continue
            if boundary is not None and created <= boundary:
                continue
            fresh.append(entry)

        fresh.sort(key=lambda entry: _created_utc(entry) or 0.0)
        return fresh

    def _message_to_inbound(
        self,
        entry: Mapping[str, object],
        *,
        provider_inbox_id: str,
    ) -> InboundMessage | None:
        """Normalize a Reddit inbox entry (message or comment-reply) into an
        inbound message. Only entries with a body and a fullname are usable;
        anything else (e.g. malformed data) is skipped rather than raised,
        so one bad entry never drops the rest of the batch."""
        data = entry.get("data")
        if not isinstance(data, dict):
            return None

        name = data.get("name")
        if not isinstance(name, str) or not name:
            return None

        body = data.get("body")
        if not isinstance(body, str):
            return None

        author = data.get("author")
        subject = data.get("subject")

        # Messages carry first_message_name once a thread has more than one
        # message; comment-kind replies carry no such field. Either way,
        # falling back to the entry's own fullname keeps a lone message
        # addressable as the root of its own (as yet single-message) thread.
        thread_root = data.get("first_message_name")
        thread_id = thread_root if isinstance(thread_root, str) and thread_root else name

        return InboundMessage(
            external_event_id=name,
            provider_inbox_id=provider_inbox_id,
            provider_message_id=name,
            provider_thread_id=thread_id,
            sender_address=author if isinstance(author, str) else None,
            subject=subject if isinstance(subject, str) else None,
            text=body,
            chat_type="private",
        )

    def _normalize_messages(
        self,
        entries: list[dict[str, object]],
        *,
        provider_inbox_id: str,
    ) -> list[InboundMessage]:
        messages = []
        for entry in entries:
            message = self._message_to_inbound(entry, provider_inbox_id=provider_inbox_id)
            if message is not None:
                messages.append(message)
        return messages

    def _mark_read(self, access_token: str, entries: list[dict[str, object]]) -> None:
        """Mark polled messages read so they drop out of /message/unread."""
        names = [
            entry["data"]["name"]
            for entry in entries
            if isinstance(entry.get("data"), dict) and isinstance(entry["data"].get("name"), str)
        ]
        if not names:
            return
        self._client.post(
            READ_MESSAGE_PATH,
            headers=self._headers(access_token),
            data={"id": ",".join(names)},
        )

    def poll_messages(
        self,
        credentials: Mapping[str, str] | None,
        cursor: str | None = None,
    ) -> tuple[list[InboundMessage], str]:
        """Poll the Reddit inbox for new, unread private messages.

        Mirrors BlueskyProvider.poll_notifications: a cold start (cursor is
        None) establishes the baseline without emitting the whole unread
        inbox as brand-new events, then every later call returns only what's
        newer than the stored cursor.
        """
        access_token = self._authenticate(credentials)
        me = self._me(access_token)
        account_id = _require_string(me, "id", MISSING_ID_ERROR)
        provider_inbox_id = f"t2_{account_id}"

        response = self._client.get(
            UNREAD_PATH,
            headers=self._headers(access_token),
            params={"limit": 100, "mark": "false"},
        )
        response.raise_for_status()
        entries = self._messages_from_listing(response.json())

        newest_cursor = self._newest_cursor(entries, cursor)

        if cursor is None:
            self._mark_read(access_token, entries)
            return [], newest_cursor or repr(time.time())

        fresh_entries = self._fresh_messages(entries, cursor)
        messages = self._normalize_messages(fresh_entries, provider_inbox_id=provider_inbox_id)
        self._mark_read(access_token, entries)

        return messages, newest_cursor or cursor

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        """Verify and normalize a forwarded batch of Reddit inbox entries.

        Reddit has no native push webhook for private messages, so this path
        is not hit by Reddit itself in production -- live inbound arrives via
        poll_messages() (see the listener manager). This exists for the same
        reason Bluesky's does: offline fakes and tests exercise the same
        shared-secret-header + normalization path a self-hosted relay could
        use to forward a poll result through the webhook pipeline.
        """
        if not self._webhook_secret:
            raise WebhookVerificationError(MISSING_WEBHOOK_SECRET_ERROR)

        received_token = lower_headers(headers).get(TOKEN_HEADER, "").encode("utf-8")
        expected_token = self._webhook_secret.encode("utf-8")

        if not hmac.compare_digest(received_token, expected_token):
            raise WebhookVerificationError(WEBHOOK_TOKEN_MISMATCH_ERROR)

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR) from exc

        if not isinstance(data, dict):
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR)

        messages = data.get("messages")
        if not isinstance(messages, list):
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR)

        entries = [message for message in messages if isinstance(message, dict)]

        provider_inbox_id = (credentials or {}).get("provider_resource_id", "")
        if not provider_inbox_id:
            raise WebhookVerificationError(MISSING_WEBHOOK_INBOX_ERROR)

        return self._normalize_messages(entries, provider_inbox_id=provider_inbox_id)
