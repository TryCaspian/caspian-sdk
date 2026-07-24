"""Bluesky adapter using the official AT Protocol XRPC APIs.

Each connected account supplies its own Bluesky identifier and app password.
The adapter authenticates through ``com.atproto.server.createSession``, creates
posts and replies through ``com.atproto.repo.createRecord``, and receives
mentions and replies by polling ``app.bsky.notification.listNotifications``.

Inbound notifications are normalized into Caspian ``InboundMessage`` objects.
Provider message identifiers contain the AT URI/CID references required for
future replies, but those Bluesky-specific details never leave this package as
public types.

Only opt-in interactions are received: mentions and replies. Likes, follows,
reposts, and other notification reasons are ignored.
"""

import base64
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime

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

SESSION_PATH = "/xrpc/com.atproto.server.createSession"
CREATE_RECORD_PATH = "/xrpc/com.atproto.repo.createRecord"
LIST_NOTIFICATIONS_PATH = "/xrpc/app.bsky.notification.listNotifications"

POST_COLLECTION = "app.bsky.feed.post"
SUPPORTED_NOTIFICATION_REASONS = frozenset({"mention", "reply"})

TOKEN_HEADER = "x-caspian-webhook-token"

MISSING_CREDENTIALS_ERROR = (
    "bluesky requires identifier and app_password in the connection credentials"
)
INVALID_SESSION_RESPONSE_ERROR = "bluesky createSession returned an invalid response"
INVALID_RECORD_RESPONSE_ERROR = "bluesky createRecord returned an invalid response"
INVALID_NOTIFICATIONS_RESPONSE_ERROR = "bluesky listNotifications returned an invalid response"

MISSING_DID_ERROR = "bluesky createSession response is missing did"
MISSING_HANDLE_ERROR = "bluesky createSession response is missing handle"
MISSING_ACCESS_TOKEN_ERROR = "bluesky createSession response is missing accessJwt"

MISSING_URI_ERROR = "bluesky createRecord response is missing uri"
MISSING_CID_ERROR = "bluesky createRecord response is missing cid"
MISSING_TEXT_ERROR = "bluesky requires a text message"

INVALID_MESSAGE_ID_ERROR = "invalid Bluesky provider_message_id"
INVALID_WEBHOOK_PAYLOAD_ERROR = "invalid Bluesky webhook payload"
WEBHOOK_TOKEN_MISMATCH_ERROR = "Bluesky webhook token mismatch"
MISSING_WEBHOOK_INBOX_ERROR = "Bluesky webhook requires a provider inbox id"
MISSING_WEBHOOK_SECRET_ERROR = "Bluesky webhook secret is not configured"
SESSION_AUTH_ERROR = "Bluesky authentication failed"


def _utc_now() -> str:
    """Return an AT Protocol-compatible UTC timestamp."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _encode_message_id(
    *,
    uri: str,
    cid: str,
    root_uri: str | None = None,
    root_cid: str | None = None,
) -> str:
    """Encode the strong references needed to reply without another lookup."""
    value = {
        "uri": uri,
        "cid": cid,
        "root_uri": root_uri or uri,
        "root_cid": root_cid or cid,
    }
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"bsky:{encoded}"


def _decode_message_id(provider_message_id: str) -> dict[str, str]:
    """Decode a Bluesky provider message identifier."""
    prefix = "bsky:"

    if not provider_message_id.startswith(prefix):
        raise ValueError(INVALID_MESSAGE_ID_ERROR)

    encoded = provider_message_id[len(prefix) :]
    padded = encoded + "=" * (-len(encoded) % 4)

    try:
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(INVALID_MESSAGE_ID_ERROR) from exc

    required = {"uri", "cid", "root_uri", "root_cid"}

    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not all(isinstance(value[key], str) and value[key] for key in required)
    ):
        raise ValueError(INVALID_MESSAGE_ID_ERROR)

    return value


class BlueskyProvider:
    """Caspian channel provider for Bluesky accounts."""

    name = "bluesky"
    channel = "bluesky"

    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
        }
    )

    connect_credentials: tuple[str, ...] = (
        "identifier",
        "app_password",
    )
    optional_connect_credentials: tuple[str, ...] = ()

    def __init__(
        self,
        base_url: str = "https://bsky.social",
        webhook_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=30.0,
        )

    def _create_session(
        self,
        credentials: Mapping[str, str] | None,
    ) -> dict[str, object]:
        """Authenticate a connected account and return its session response."""
        creds = credentials or {}

        identifier = creds.get("identifier")
        app_password = creds.get("app_password")

        if not identifier or not app_password:
            raise ValueError(MISSING_CREDENTIALS_ERROR)

        response = self._client.post(
            SESSION_PATH,
            json={
                "identifier": identifier,
                "password": app_password,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ValueError(SESSION_AUTH_ERROR) from exc

        session = response.json()

        if not isinstance(session, dict):
            raise ValueError(INVALID_SESSION_RESPONSE_ERROR)

        return session

    def provision(
        self,
        request: ProvisionRequest,
    ) -> ProvisionResult:
        """Validate credentials and provision the connected Bluesky account."""
        session = self._create_session(request.credentials)

        did = self._require_string(
            session,
            "did",
            MISSING_DID_ERROR,
        )
        handle = self._require_string(
            session,
            "handle",
            MISSING_HANDLE_ERROR,
        )

        return ProvisionResult(
            address=handle,
            provider_resource_id=did,
        )

    def _create_post(
        self,
        *,
        access_token: str,
        repo: str,
        text: str,
        reply: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create a Bluesky post and return the provider response."""
        record: dict[str, object] = {
            "$type": POST_COLLECTION,
            "text": text,
            "createdAt": _utc_now(),
        }

        if reply is not None:
            record["reply"] = reply

        payload = {
            "repo": repo,
            "collection": POST_COLLECTION,
            "record": record,
        }

        response = self._client.post(
            CREATE_RECORD_PATH,
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            json=payload,
        )
        response.raise_for_status()

        result = response.json()

        if not isinstance(result, dict):
            raise ValueError(INVALID_RECORD_RESPONSE_ERROR)

        return result

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        """Create a new Bluesky post."""
        del provider_inbox_id

        if not message.text:
            raise ValueError(MISSING_TEXT_ERROR)

        session = self._create_session(credentials)

        access_token = self._require_string(
            session,
            "accessJwt",
            MISSING_ACCESS_TOKEN_ERROR,
        )
        did = self._require_string(
            session,
            "did",
            MISSING_DID_ERROR,
        )

        result = self._create_post(
            access_token=access_token,
            repo=did,
            text=message.text,
        )

        uri = self._require_string(
            result,
            "uri",
            MISSING_URI_ERROR,
        )
        cid = self._require_string(
            result,
            "cid",
            MISSING_CID_ERROR,
        )

        provider_message_id = _encode_message_id(
            uri=uri,
            cid=cid,
        )

        return SendResult(
            provider_message_id=provider_message_id,
            provider_thread_id=provider_message_id,
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        """Reply to an existing Bluesky post."""
        del provider_inbox_id

        if not message.text:
            raise ValueError(MISSING_TEXT_ERROR)

        session = self._create_session(credentials)

        access_token = self._require_string(
            session,
            "accessJwt",
            MISSING_ACCESS_TOKEN_ERROR,
        )
        did = self._require_string(
            session,
            "did",
            MISSING_DID_ERROR,
        )

        parent = _decode_message_id(provider_message_id)

        reply_reference: dict[str, object] = {
            "root": {
                "uri": parent["root_uri"],
                "cid": parent["root_cid"],
            },
            "parent": {
                "uri": parent["uri"],
                "cid": parent["cid"],
            },
        }

        result = self._create_post(
            access_token=access_token,
            repo=did,
            text=message.text,
            reply=reply_reference,
        )

        uri = self._require_string(
            result,
            "uri",
            MISSING_URI_ERROR,
        )
        cid = self._require_string(
            result,
            "cid",
            MISSING_CID_ERROR,
        )

        new_message_id = _encode_message_id(
            uri=uri,
            cid=cid,
            root_uri=parent["root_uri"],
            root_cid=parent["root_cid"],
        )

        thread_id = _encode_message_id(
            uri=parent["root_uri"],
            cid=parent["root_cid"],
        )

        return SendResult(
            provider_message_id=new_message_id,
            provider_thread_id=thread_id,
        )

    def _require_string(
        self,
        data: Mapping[str, object],
        key: str,
        error_message: str,
    ) -> str:
        """Return a required non-empty string value."""
        value = data.get(key)

        if not isinstance(value, str) or not value:
            raise ValueError(error_message)

        return value

    def _extract_notification_fields(
        self,
        notification: Mapping[str, object],
    ) -> tuple[str, str, dict[str, object], dict[str, object]] | None:
        """Extract the required fields from a Bluesky notification."""
        uri = notification.get("uri")
        cid = notification.get("cid")
        author = notification.get("author")
        record = notification.get("record")

        if not isinstance(uri, str) or not uri:
            return None

        if not isinstance(cid, str) or not cid:
            return None

        if not isinstance(author, dict):
            return None

        if not isinstance(record, dict):
            return None

        return uri, cid, author, record

    def _notification_root_reference(
        self,
        record: Mapping[str, object],
        *,
        fallback_uri: str,
        fallback_cid: str,
    ) -> tuple[str, str]:
        """Return the thread root reference for a notification record."""
        reply = record.get("reply")

        if not isinstance(reply, dict):
            return fallback_uri, fallback_cid

        root = reply.get("root")

        if not isinstance(root, dict):
            return fallback_uri, fallback_cid

        root_uri = root.get("uri")
        root_cid = root.get("cid")

        if not isinstance(root_uri, str) or not root_uri:
            root_uri = fallback_uri

        if not isinstance(root_cid, str) or not root_cid:
            root_cid = fallback_cid

        return root_uri, root_cid

    def _notifications_from_payload(
        self,
        payload: object,
        *,
        error_message: str,
    ) -> list[dict[str, object]]:
        """Extract notification objects from a Bluesky listNotifications payload."""
        if not isinstance(payload, dict):
            raise ValueError(error_message)

        notifications = payload.get("notifications")

        if not isinstance(notifications, list):
            raise ValueError(error_message)

        return [notification for notification in notifications if isinstance(notification, dict)]

    def _fetch_notifications(
        self,
        *,
        access_token: str,
        boundary: str | None = None,
    ) -> list[dict[str, object]]:
        """Fetch mention and reply notifications until the boundary or page end."""
        notifications: list[dict[str, object]] = []
        api_cursor: str | None = None

        while True:
            params: list[tuple[str, str]] = [
                ("limit", "50"),
                ("reasons", "mention"),
                ("reasons", "reply"),
            ]

            if api_cursor:
                params.append(("cursor", api_cursor))

            response = self._client.get(
                LIST_NOTIFICATIONS_PATH,
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
                params=params,
            )
            response.raise_for_status()

            data = response.json()

            page = self._notifications_from_payload(
                data,
                error_message=INVALID_NOTIFICATIONS_RESPONSE_ERROR,
            )
            notifications.extend(page)

            if boundary and any(
                isinstance(notification.get("indexedAt"), str)
                and notification["indexedAt"] <= boundary
                for notification in page
            ):
                break

            next_cursor = data.get("cursor") if isinstance(data, dict) else None

            if not isinstance(next_cursor, str) or not next_cursor or not page:
                break

            api_cursor = next_cursor

        return notifications

    def _normalize_notifications(
        self,
        notifications: list[dict[str, object]],
        *,
        provider_inbox_id: str,
    ) -> list[InboundMessage]:
        """Normalize supported Bluesky notifications into inbound messages."""
        messages: list[InboundMessage] = []

        for notification in notifications:
            if notification.get("reason") not in SUPPORTED_NOTIFICATION_REASONS:
                continue

            message = self._notification_to_inbound(
                notification=notification,
                provider_inbox_id=provider_inbox_id,
            )

            if message is not None:
                messages.append(message)

        return messages

    def _newest_notification_cursor(
        self,
        notifications: list[dict[str, object]],
        current_cursor: str | None,
    ) -> str | None:
        """Return the newest indexedAt timestamp."""
        timestamps = [
            indexed_at
            for notification in notifications
            if isinstance(indexed_at := notification.get("indexedAt"), str) and indexed_at
        ]

        if current_cursor is not None:
            timestamps.append(current_cursor)

        return max(timestamps, default=None)

    def _fresh_notifications(
        self,
        notifications: list[dict[str, object]],
        cursor: str,
    ) -> list[dict[str, object]]:
        """Return supported notifications newer than the stored cursor."""
        fresh = []

        for notification in notifications:
            if notification.get("reason") not in SUPPORTED_NOTIFICATION_REASONS:
                continue

            indexed_at = notification.get("indexedAt")

            if not isinstance(indexed_at, str) or indexed_at <= cursor:
                continue

            fresh.append(notification)

        fresh.sort(key=lambda item: str(item.get("indexedAt", "")))
        return fresh

    def _notification_to_inbound(
        self,
        *,
        notification: dict[str, object],
        provider_inbox_id: str,
    ) -> InboundMessage | None:
        """Normalize a Bluesky mention or reply into an inbound message."""
        fields = self._extract_notification_fields(notification)

        if fields is None:
            return None

        uri, cid, author, record = fields

        if author.get("did") == provider_inbox_id:
            return None

        text = record.get("text")

        if not isinstance(text, str):
            return None

        root_uri, root_cid = self._notification_root_reference(
            record,
            fallback_uri=uri,
            fallback_cid=cid,
        )

        handle = author.get("handle")
        display_name = author.get("displayName")

        return InboundMessage(
            external_event_id=uri,
            provider_inbox_id=provider_inbox_id,
            provider_message_id=_encode_message_id(
                uri=uri,
                cid=cid,
                root_uri=root_uri,
                root_cid=root_cid,
            ),
            provider_thread_id=_encode_message_id(
                uri=root_uri,
                cid=root_cid,
            ),
            sender_address=handle if isinstance(handle, str) else None,
            sender_name=display_name if isinstance(display_name, str) else None,
            text=text,
            chat_type="public",
        )

    def poll_notifications(
        self,
        credentials: Mapping[str, str] | None,
        cursor: str | None = None,
    ) -> tuple[list[InboundMessage], str]:
        """Poll Bluesky for new mentions and replies."""
        session = self._create_session(credentials)

        access_token = self._require_string(
            session,
            "accessJwt",
            MISSING_ACCESS_TOKEN_ERROR,
        )
        did = self._require_string(
            session,
            "did",
            MISSING_DID_ERROR,
        )

        notifications = self._fetch_notifications(
            access_token=access_token,
            boundary=cursor,
        )

        newest_cursor = self._newest_notification_cursor(
            notifications,
            cursor,
        )

        if cursor is None:
            return [], newest_cursor or _utc_now()

        fresh_notifications = self._fresh_notifications(
            notifications,
            cursor,
        )

        messages = self._normalize_notifications(
            fresh_notifications,
            provider_inbox_id=did,
        )

        return messages, newest_cursor or cursor

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        """Verify and normalize a delivered Bluesky notification payload."""
        if not self._webhook_secret:
            raise WebhookVerificationError(MISSING_WEBHOOK_SECRET_ERROR)

        received_token = lower_headers(headers).get(
            TOKEN_HEADER,
            "",
        )

        if not hmac.compare_digest(received_token, self._webhook_secret):
            raise WebhookVerificationError(WEBHOOK_TOKEN_MISMATCH_ERROR)

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR) from exc

        try:
            notifications = self._notifications_from_payload(
                data,
                error_message=INVALID_WEBHOOK_PAYLOAD_ERROR,
            )
        except ValueError as exc:
            raise WebhookVerificationError(INVALID_WEBHOOK_PAYLOAD_ERROR) from exc

        provider_inbox_id = (credentials or {}).get(
            "provider_resource_id",
            "",
        )

        if not provider_inbox_id:
            raise WebhookVerificationError(MISSING_WEBHOOK_INBOX_ERROR)

        return self._normalize_notifications(
            notifications,
            provider_inbox_id=provider_inbox_id,
        )
