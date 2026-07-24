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
        newest = cursor
        fresh: list[tuple[str, InboundMessage]] = []
        for post_urn in tracked_posts:
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
                    if cursor is not None and _cursor_after(token, cursor):
                        messages = parse_comments_page(
                            {"elements": [comment]},
                            organization_urn,
                            post_urn=post_urn,
                        )
                        fresh.extend((token, message) for message in messages)
                if len(elements) < COMMENT_PAGE_SIZE:
                    break
                start += COMMENT_PAGE_SIZE
        if cursor is None:
            return [], newest or "0:"
        fresh.sort(key=lambda item: item[0])
        return [message for _, message in fresh], newest or cursor

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
            data.get("organizationUrn")
            or data.get("organization_urn")
            or self._organization(credentials)
        )
        post_urn = data.get("postUrn") or data.get("object")
        return parse_comments_page(data, organization_urn, post_urn=post_urn)


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
        newest = cursor
        fresh: list[tuple[str, InboundMessage]] = []
        for post_urn in tracked:
            for comment in self.comments.get(post_urn, []):
                token = _comment_cursor(comment)
                if _cursor_after(token, newest):
                    newest = token
                if cursor is not None and _cursor_after(token, cursor):
                    messages = parse_comments_page(
                        {"elements": [comment]},
                        organization_urn,
                        post_urn=post_urn,
                    )
                    fresh.extend((token, message) for message in messages)
        if cursor is None:
            return [], newest or "0:"
        fresh.sort(key=lambda item: item[0])
        return [message for _, message in fresh], newest or cursor

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        return LinkedInProvider(webhook_secret=self.webhook_secret).parse_webhook(
            payload,
            headers,
            credentials={"organization_urn": self.organization_urn},
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
