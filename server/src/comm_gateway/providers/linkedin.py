"""LinkedIn adapter using the official REST APIs for posts and comments.

Surface choice: organization posts/comments, not member messaging. LinkedIn
messaging requires partner approval; Community Management APIs are the realistic
first channel surface. Inbound is polling comments on tracked post URNs. The
webhook parser is still implemented for signed LinkedIn push/test payloads, but
the normal runtime path is poll_comments().

Access tier: organization social feed permissions, typically
``r_organization_social_feed`` and ``w_organization_social_feed`` with a member
who can administer or post for the company page.
"""

import hashlib
import hmac
import json
import urllib.parse
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

API = "https://api.linkedin.com"
DEFAULT_VERSION = "202605"
SIGNATURE_HEADER = "x-li-signature"
PROTOCOL_VERSION = "2.0.0"
COMMENT_PAGE_SIZE = 50


def _quote_urn(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _tracked_posts(value: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return tuple(str(part).strip() for part in value if str(part).strip())


def _message_text(comment: dict) -> str | None:
    message = comment.get("message") or {}
    return message.get("text")


def _comment_time(comment: dict) -> int:
    created = comment.get("created") or {}
    value = created.get("time") or comment.get("createdAt") or comment.get("lastModifiedAt")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _comment_cursor(comment: dict) -> str:
    return f"{_comment_time(comment)}:{comment.get('id', '')}"


def _cursor_after(left: str, right: str | None) -> bool:
    if right is None:
        return True
    left_time, _, left_id = left.partition(":")
    right_time, _, right_id = right.partition(":")
    try:
        left_key = (int(left_time), left_id)
        right_key = (int(right_time), right_id)
    except ValueError:
        return left > right
    return left_key > right_key


def _poll_cursors(cursor: str | None, tracked_posts: tuple[str, ...]) -> dict[str, str]:
    if not cursor:
        return {}
    try:
        decoded = json.loads(cursor)
    except (TypeError, ValueError):
        return dict.fromkeys(tracked_posts, cursor)
    if isinstance(decoded, dict):
        return {
            post_urn: value
            for post_urn, value in decoded.items()
            if post_urn in tracked_posts and isinstance(value, str)
        }
    return dict.fromkeys(tracked_posts, cursor)


def _encode_poll_cursors(cursors: Mapping[str, str]) -> str:
    return json.dumps(cursors, sort_keys=True, separators=(",", ":"))


def encode_provider_message_id(post_urn: str, comment_id: str, comment_urn: str = "") -> str:
    return "|".join((post_urn, comment_id, comment_urn))


def decode_provider_message_id(provider_message_id: str) -> tuple[str, str, str]:
    parts = provider_message_id.split("|", 2)
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], parts[2]


def parse_comments_page(
    data: dict,
    organization_urn: str,
    *,
    post_urn: str | None = None,
    include_own: bool = False,
) -> list[InboundMessage]:
    """Normalize a LinkedIn socialActions comments collection."""
    out: list[InboundMessage] = []
    default_post_urn = post_urn or data.get("postUrn") or data.get("object")
    for comment in data.get("elements", []) or []:
        object_urn = comment.get("object") or default_post_urn
        comment_id = comment.get("id")
        if not object_urn or not comment_id:
            continue
        actor = comment.get("actor") or (comment.get("created") or {}).get("actor")
        if actor == organization_urn and not include_own:
            continue
        text = _message_text(comment)
        if not text:
            continue
        comment_urn = comment.get("commentUrn", "")
        out.append(
            InboundMessage(
                external_event_id=comment_urn or f"{object_urn}:{comment_id}",
                provider_inbox_id=organization_urn,
                provider_message_id=encode_provider_message_id(
                    object_urn, comment_id, comment_urn
                ),
                provider_thread_id=object_urn,
                sender_address=actor,
                subject=object_urn,
                text=text,
                chat_type="linkedin_comment",
            )
        )
    return out


class LinkedInProvider:
    name = "linkedin"
    channel = "linkedin"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    connect_credentials: tuple[str, ...] = ("access_token", "organization_urn")
    optional_connect_credentials: tuple[str, ...] = ("tracked_posts", "webhook_secret")

    def __init__(
        self,
        access_token: str = "",
        organization_urn: str = "",
        tracked_posts: str = "",
        webhook_secret: str = "",
        base_url: str = API,
        version: str = DEFAULT_VERSION,
    ) -> None:
        self._access_token = access_token
        self._organization_urn = organization_urn
        self._tracked_posts = _tracked_posts(tracked_posts)
        self._webhook_secret = webhook_secret
        self._base_url = base_url.rstrip("/")
        self._version = version
        self._client = httpx.Client(base_url=self._base_url, timeout=30.0)

    def _token(self, credentials: Mapping[str, str] | None) -> str:
        token = (credentials or {}).get("access_token") or self._access_token
        if not token:
            raise ValueError("linkedin needs an OAuth access_token credential")
        return token

    def _organization(self, credentials: Mapping[str, str] | None) -> str:
        organization = (credentials or {}).get("organization_urn") or self._organization_urn
        if not organization:
            raise ValueError("linkedin needs an organization_urn credential")
        return organization

    def _secret(self, credentials: Mapping[str, str] | None) -> str:
        return (credentials or {}).get("webhook_secret") or self._webhook_secret

    def _headers(self, credentials: Mapping[str, str] | None, *, json_body: bool = False) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token(credentials)}",
            "Linkedin-Version": self._version,
            "X-Restli-Protocol-Version": PROTOCOL_VERSION,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _tracked(self, credentials: Mapping[str, str] | None) -> tuple[str, ...]:
        return (
            _tracked_posts((credentials or {}).get("tracked_posts"))
            or self._tracked_posts
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        organization_urn = self._organization(request.credentials)
        return ProvisionResult(
            address=f"linkedin:{organization_urn.rsplit(':', 1)[-1]}",
            provider_resource_id=organization_urn,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        organization_urn = self._organization(credentials)
        body = {
            "author": organization_urn,
            "commentary": message.text or message.html or "",
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        response = self._client.post(
            "/rest/posts",
            headers=self._headers(credentials, json_body=True),
            json=body,
        )
        response.raise_for_status()
        post_urn = response.headers.get("x-restli-id")
        if not post_urn:
            try:
                post_urn = response.json().get("id")
            except ValueError:
                post_urn = None
        if not post_urn:
            raise RuntimeError("LinkedIn post creation did not return x-restli-id")
        return SendResult(provider_message_id=post_urn, provider_thread_id=post_urn)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        organization_urn = self._organization(credentials)
        post_urn, _, comment_urn = decode_provider_message_id(provider_message_id)
        target_urn = comment_urn or post_urn
        body = {
            "actor": organization_urn,
            "object": post_urn,
            "message": {"text": message.text or message.html or ""},
        }
        if comment_urn:
            body["parentComment"] = comment_urn
        response = self._client.post(
            f"/rest/socialActions/{_quote_urn(target_urn)}/comments",
            headers=self._headers(credentials, json_body=True),
            json=body,
        )
        response.raise_for_status()
        comment_id = response.headers.get("x-restli-id")
        comment_data: dict = {}
        try:
            comment_data = response.json()
        except ValueError:
            pass
        comment_id = comment_id or comment_data.get("id")
        if not comment_id:
            raise RuntimeError("LinkedIn comment creation did not return x-restli-id")
        new_comment_urn = comment_data.get("commentUrn", "")
        return SendResult(
            provider_message_id=encode_provider_message_id(
                post_urn, comment_id, new_comment_urn
            ),
            provider_thread_id=post_urn,
        )

    def poll_comments(
        self,
        credentials: Mapping[str, str] | None,
        cursor: str | None = None,
    ) -> tuple[list[InboundMessage], str]:
        organization_urn = self._organization(credentials)
        tracked_posts = self._tracked(credentials)
        if not tracked_posts:
            raise ValueError("linkedin poll_comments needs tracked post URNs")
        cursors = _poll_cursors(cursor, tracked_posts)
        fresh: list[tuple[str, InboundMessage]] = []
        for post_urn in tracked_posts:
            post_cursor = cursors.get(post_urn)
            newest = post_cursor
            start = 0
            while True:
                response = self._client.get(
                    f"/rest/socialActions/{_quote_urn(post_urn)}/comments",
                    headers=self._headers(credentials),
                    params={"count": COMMENT_PAGE_SIZE, "start": start},
                )
                response.raise_for_status()
                data = response.json()
                elements = data.get("elements", []) or []
                for comment in elements:
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
                if len(elements) < COMMENT_PAGE_SIZE:
                    break
                start += COMMENT_PAGE_SIZE
            cursors[post_urn] = newest or post_cursor or "0:"
        fresh.sort(key=lambda item: item[0])
        if cursor is None:
            return [], _encode_poll_cursors(cursors)
        return [message for _, message in fresh], _encode_poll_cursors(cursors)

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return data.get("organizationUrn") or data.get("organization_urn")

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = self._secret(credentials)
        if not secret:
            raise WebhookVerificationError("LinkedIn webhook secret missing")
        received = lower_headers(headers).get(SIGNATURE_HEADER, "")
        expected = hmac.new(
            secret.encode(),
            b"hmacsha256=" + payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(received, expected):
            raise WebhookVerificationError("LinkedIn signature mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        organization_urn = (
            (credentials or {}).get("organization_urn")
            or data.get("organizationUrn")
            or data.get("organization_urn")
            or self._organization(credentials)
        )
        post_urn = data.get("postUrn") or data.get("object")
        return parse_comments_page(data, organization_urn, post_urn=post_urn)
