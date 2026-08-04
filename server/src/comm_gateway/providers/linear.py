"""Linear Channel Adapter for issue comments and issue updates.

Linear delivers issue and comment webhooks to configured endpoint URLs.
Comments on issues normalize into Caspian conversations whose provider_thread_id
is the Linear issue identifier (e.g., "ENG-123") or issue ID.
"""

import hashlib
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
    split_composite_id,
)

API = "https://api.linear.app"
MAX_TIMESTAMP_SKEW = 60 * 5  # Reject webhook deliveries older than 5 minutes


def verify_linear_timestamp(data: dict) -> None:
    """Verify that a Linear webhook payload timestamp falls within MAX_TIMESTAMP_SKEW."""
    if isinstance(data, dict) and "webhookTimestamp" in data:
        try:
            raw_ts = float(data["webhookTimestamp"])
            ts_sec = raw_ts / 1000.0 if raw_ts > 1e11 else raw_ts
            skew = abs(time.time() - ts_sec)
        except (ValueError, TypeError):
            raise WebhookVerificationError("Linear timestamp invalid") from None
        if skew > MAX_TIMESTAMP_SKEW:
            raise WebhookVerificationError("Linear timestamp too old")


def parse_linear_comment(data: dict, delivery_id: str = "") -> list[InboundMessage]:
    """Normalize a created Linear ``Comment`` webhook into Caspian's InboundMessage schema."""
    if not isinstance(data, dict):
        return []
    if data.get("type") != "Comment" or data.get("action") != "create":
        return []

    comment = data.get("data")
    if not isinstance(comment, dict):
        return []

    actor = data.get("actor")
    if isinstance(actor, dict):
        # Ignore bot, app, or system triggered actions to prevent response loops
        if actor.get("type") in ("app", "system") or actor.get("isBot"):
            return []

    body = comment.get("body")
    comment_id = comment.get("id")
    if not body or not comment_id:
        return []

    issue = comment.get("issue")
    issue_identifier = None
    if isinstance(issue, dict):
        issue_identifier = issue.get("identifier") or issue.get("id")
    if not issue_identifier:
        issue_identifier = comment.get("issueId")

    organization_id = str(data.get("organizationId") or "")

    if not issue_identifier:
        return []

    thread_id = str(issue_identifier)
    user = comment.get("user")
    if not isinstance(user, dict):
        user = actor if isinstance(actor, dict) else {}

    sender_address = str(user.get("email") or user.get("id") or user.get("name") or "unknown")
    sender_name = str(user.get("name") or user.get("displayName") or sender_address)

    return [
        InboundMessage(
            external_event_id=delivery_id or f"linear:{comment_id}",
            provider_inbox_id=organization_id,
            provider_message_id=f"{thread_id}:{comment_id}",
            provider_thread_id=thread_id,
            sender_address=sender_address,
            sender_name=sender_name,
            text=body,
            chat_type="issue",
        )
    ]


class LinearProvider:
    name = "linear"
    channel = "linear"
    connect_credentials = ("organization_id",)
    optional_connect_credentials = ("api_key", "webhook_secret")
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        webhook_secret: str = "",
        base_url: str = API,
    ) -> None:
        self._client_id = client_id
        self.client_secret = client_secret
        self.webhook_secret = webhook_secret
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    @property
    def client_id(self) -> str:
        """Compatibility marker used by gateways to detect a configured App."""
        return self._client_id

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        """Route an untrusted webhook delivery by Linear organizationId before verification."""
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        org_id = data.get("organizationId") or (data.get("data") or {}).get("organizationId")
        return str(org_id) if org_id is not None else None

    def _verify_signature(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        """Verify the Linear request signature against Linear-Signature header."""
        header_map = lower_headers(headers)
        signature = header_map.get("linear-signature", "")
        if not signature:
            raise WebhookVerificationError("Linear signature header missing")

        webhook_secret = (credentials or {}).get("webhook_secret") or self.webhook_secret
        if not webhook_secret:
            raise WebhookVerificationError("Linear webhook secret is not configured")

        expected = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise WebhookVerificationError("Linear signature mismatch")

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        header_map = lower_headers(headers)
        self._verify_signature(payload, headers, credentials)

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc

        verify_linear_timestamp(data)

        delivery_id = header_map.get("linear-delivery", "") or header_map.get("x-delivery", "")
        return parse_linear_comment(data, delivery_id=delivery_id)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        """Return provisioning details for connection setup."""
        credentials = request.credentials or {}
        return ProvisionResult(
            address=credentials.get("address", "linear"),
            provider_resource_id=credentials.get(
                "provider_resource_id", credentials.get("organization_id", "")
            ),
        )

    def _post_comment(
        self,
        thread_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        """Post a comment to a Linear issue via Linear's GraphQL API."""
        creds = credentials or {}
        api_key = creds.get("api_key") or creds.get("access_token") or creds.get("linear_api_key")
        if not api_key:
            raise ValueError("Linear API key / access token is required for outbound messaging")

        if not thread_id:
            raise ValueError("Linear comment creation requires a valid issue thread ID")

        query = """
        mutation CreateComment($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
            comment {
              id
            }
          }
        }
        """
        variables = {"issueId": thread_id, "body": message.text or ""}

        # Support Linear Personal API Keys (lin_api_...) and OAuth Bearer Tokens
        if api_key.startswith("Bearer "):
            auth_header = api_key
        elif api_key.startswith("lin_api_"):
            auth_header = api_key
        else:
            auth_header = f"Bearer {api_key}"

        headers = {
            "Content-Type": "application/json",
            "Authorization": auth_header,
        }

        try:
            response = self._client.post(
                "/graphql",
                json={"query": query, "variables": variables},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Linear HTTP request failed: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from Linear API: {exc}") from exc

        if data.get("errors"):
            errors = data["errors"]
            msg = errors[0].get("message") if errors else "GraphQL error"
            raise RuntimeError(f"Linear API error: {msg}")

        result = (data.get("data") or {}).get("commentCreate") or {}
        if not result.get("success") or not result.get("comment"):
            raise RuntimeError(f"Linear comment creation failed: {data}")

        comment_id = result["comment"]["id"]
        return SendResult(
            provider_message_id=f"{thread_id}:{comment_id}",
            provider_thread_id=thread_id,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        if not message.to:
            raise ValueError("Linear send requires target issue in message.to")
        thread_id = message.to[0].strip()
        return self._post_comment(thread_id, message, credentials)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        thread_id, _ = split_composite_id(provider_message_id)
        return self._post_comment(thread_id, message, credentials)
