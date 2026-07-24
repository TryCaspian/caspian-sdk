"""Linear adapter (GraphQL API outbound, webhooks inbound).

Linear issues and comments are treated as the conversation surface: new
issues/comments arrive as inbound messages, and replies create Linear comments
on the source issue.
"""

import hashlib
import hmac
import json
import secrets
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

API = "https://api.linear.app/graphql"
SIGNATURE_HEADER = "linear-signature"
MAX_WEBHOOK_TIMESTAMP_SKEW_MS = 60_000

ORGANIZATION_QUERY = """
query CaspianLinearOrganization {
  organization {
    id
    name
    urlKey
  }
}
"""

ISSUE_CREATE_MUTATION = """
mutation CaspianIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id
      identifier
      url
    }
  }
}
"""

COMMENT_CREATE_MUTATION = """
mutation CaspianCommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment {
      id
      url
      issue {
        id
      }
    }
  }
}
"""


def _sender(data: dict) -> tuple[str | None, str | None]:
    user = data.get("user") or data.get("creator") or {}
    address = user.get("email") or user.get("id")
    name = user.get("name") or user.get("displayName")
    return address, name


def _subject(issue: dict) -> str | None:
    identifier = issue.get("identifier")
    title = issue.get("title")
    if identifier and title:
        return f"{identifier}: {title}"
    return title or identifier


def _text_or_none(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def issue_id_from_provider_message_id(provider_message_id: str) -> str:
    issue_id, sep, _ = provider_message_id.partition(":")
    return issue_id if sep else provider_message_id


def parse_linear_webhook(data: dict, delivery_id: str | None = None) -> list[InboundMessage]:
    event_type = data.get("type")
    action = data.get("action", "")
    resource = data.get("data") or {}
    organization_id = data.get("organizationId") or resource.get("organization", {}).get("id")
    if not organization_id:
        return []
    if action != "create":
        return []

    if event_type == "Issue":
        issue_id = resource.get("id")
        if not issue_id:
            return []
        sender_address, sender_name = _sender(resource)
        title = resource.get("title")
        description = resource.get("description")
        return [
            InboundMessage(
                external_event_id=delivery_id
                or f"linear:issue:{action}:{issue_id}:{data.get('webhookTimestamp', '')}",
                provider_inbox_id=organization_id,
                provider_message_id=issue_id,
                provider_thread_id=issue_id,
                sender_address=sender_address,
                sender_name=sender_name,
                subject=_subject(resource),
                text=_text_or_none(description, title),
                chat_type="linear_issue",
            )
        ]

    if event_type == "Comment":
        comment_id = resource.get("id")
        issue = resource.get("issue") or {}
        issue_id = issue.get("id") or resource.get("issueId")
        if not comment_id or not issue_id:
            return []
        sender_address, sender_name = _sender(resource)
        return [
            InboundMessage(
                external_event_id=delivery_id
                or f"linear:comment:{action}:{comment_id}:{data.get('webhookTimestamp', '')}",
                provider_inbox_id=organization_id,
                provider_message_id=f"{issue_id}:{comment_id}",
                provider_thread_id=issue_id,
                sender_address=sender_address,
                sender_name=sender_name,
                subject=_subject(issue),
                text=resource.get("body"),
                chat_type="linear_comment",
            )
        ]

    return []


def _title_and_body(message: OutboundMessage) -> tuple[str, str]:
    text = message.text or message.html or ""
    lines = text.splitlines()
    title = message.subject or (lines[0][:120] if lines else "") or "Caspian message"
    return title, text


def _validate_webhook_timestamp(data: dict, *, now_ms: int | None = None) -> None:
    timestamp = data.get("webhookTimestamp")
    try:
        timestamp_ms = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise WebhookVerificationError("Linear webhook timestamp missing or invalid") from exc
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now_ms - timestamp_ms) > MAX_WEBHOOK_TIMESTAMP_SKEW_MS:
        raise WebhookVerificationError("Linear webhook timestamp outside freshness window")


class LinearProvider:
    name = "linear"
    channel = "linear"
    # Deployments can configure a shared Linear API key/webhook secret, while
    # self-hosted users may bring per-connection credentials.
    connect_credentials = ()
    optional_connect_credentials = ("api_key", "webhook_secret", "team_id", "organization_id")
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        api_key: str = "",
        webhook_secret: str = "",
        base_url: str = API,
    ) -> None:
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        self._base_url = base_url
        self._client = httpx.Client(timeout=30.0)

    def _token(self, credentials: Mapping[str, str] | None) -> str:
        token = (credentials or {}).get("api_key") or self._api_key
        if not token:
            raise ValueError("connection is missing a Linear api_key credential")
        return token

    def _secret(self, credentials: Mapping[str, str] | None) -> str:
        return (credentials or {}).get("webhook_secret") or self._webhook_secret

    def _graphql(
        self,
        token: str,
        query: str,
        variables: dict | None = None,
    ) -> dict:
        response = self._client.post(
            self._base_url,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": token},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            message = data["errors"][0].get("message", "Linear GraphQL error")
            raise RuntimeError(message)
        return data.get("data") or {}

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = request.credentials or {}
        organization_id = credentials.get("organization_id")
        if organization_id:
            name = credentials.get("organization_name") or credentials.get("url_key")
            return ProvisionResult(
                address=f"linear:{name or organization_id}",
                provider_resource_id=organization_id,
            )
        data = self._graphql(self._token(credentials), ORGANIZATION_QUERY)
        organization = data.get("organization") or {}
        organization_id = organization.get("id", "")
        name = organization.get("urlKey") or organization.get("name") or organization_id
        return ProvisionResult(
            address=f"linear:{name}",
            provider_resource_id=organization_id,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        credentials = credentials or {}
        team_id = message.to[0] if message.to else credentials.get("team_id")
        if not team_id:
            raise ValueError("Linear send needs message.to[0] or team_id credential")
        title, body = _title_and_body(message)
        data = self._graphql(
            self._token(credentials),
            ISSUE_CREATE_MUTATION,
            {"input": {"teamId": team_id, "title": title, "description": body}},
        )
        result = data.get("issueCreate") or {}
        if not result.get("success"):
            raise RuntimeError("Linear issueCreate failed")
        issue = result.get("issue") or {}
        issue_id = issue["id"]
        return SendResult(provider_message_id=issue_id, provider_thread_id=issue_id)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        issue_id = issue_id_from_provider_message_id(provider_message_id)
        data = self._graphql(
            self._token(credentials),
            COMMENT_CREATE_MUTATION,
            {"input": {"issueId": issue_id, "body": message.text or message.html or ""}},
        )
        result = data.get("commentCreate") or {}
        if not result.get("success"):
            raise RuntimeError("Linear commentCreate failed")
        comment = result.get("comment") or {}
        comment_id = comment["id"]
        return SendResult(
            provider_message_id=f"{issue_id}:{comment_id}",
            provider_thread_id=issue_id,
        )

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        resource = data.get("data") or {}
        return data.get("organizationId") or resource.get("organization", {}).get("id")

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        secret = self._secret(credentials)
        if not secret:
            raise WebhookVerificationError("Linear webhook secret missing")
        signature = lower_headers(headers).get(SIGNATURE_HEADER, "")
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookVerificationError("Linear signature mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        _validate_webhook_timestamp(data)
        return parse_linear_webhook(data, lower_headers(headers).get("linear-delivery"))


class FakeLinearProvider:
    name = "fake-linear"
    channel = "linear"
    capabilities = LinearProvider.capabilities
    connect_credentials = ()

    route_key = staticmethod(LinearProvider.route_key)

    def __init__(self) -> None:
        self.organization_id = f"org_{secrets.token_hex(4)}"
        self.team_id = f"team_{secrets.token_hex(4)}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=f"linear:{self.organization_id}",
            provider_resource_id=self.organization_id,
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        team_id = message.to[0] if message.to else self.team_id
        self._seq += 1
        issue_id = f"issue_{self._seq}"
        self.sent.append({"team_id": team_id, "subject": message.subject, "text": message.text})
        return SendResult(provider_message_id=issue_id, provider_thread_id=issue_id)

    def reply(
        self,
        provider_inbox_id,
        provider_message_id,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        issue_id = issue_id_from_provider_message_id(provider_message_id)
        self._seq += 1
        comment_id = f"comment_{self._seq}"
        self.replies.append({"issue_id": issue_id, "text": message.text})
        return SendResult(
            provider_message_id=f"{issue_id}:{comment_id}",
            provider_thread_id=issue_id,
        )

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        return parse_linear_webhook(data, headers.get("Linear-Delivery"))

    def issue_payload(self, *, title="Bug in checkout", description="Payment fails") -> dict:
        self._seq += 1
        return {
            "action": "create",
            "type": "Issue",
            "organizationId": self.organization_id,
            "webhookTimestamp": 1_752_000_000_000 + self._seq,
            "data": {
                "id": f"issue_{self._seq}",
                "identifier": f"LIN-{self._seq}",
                "title": title,
                "description": description,
                "creator": {
                    "id": "user_1",
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                },
            },
        }

    def comment_payload(self, *, issue_id="issue_1", body="Any update?") -> dict:
        self._seq += 1
        return {
            "action": "create",
            "type": "Comment",
            "organizationId": self.organization_id,
            "webhookTimestamp": 1_752_000_000_000 + self._seq,
            "data": {
                "id": f"comment_{self._seq}",
                "body": body,
                "issue": {"id": issue_id, "identifier": "LIN-1", "title": "Bug"},
                "user": {"id": "user_2", "name": "Grace Hopper"},
            },
        }
