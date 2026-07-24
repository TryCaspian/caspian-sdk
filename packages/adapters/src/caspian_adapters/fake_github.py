"""In-memory GitHub provider using real ``issue_comment`` payload shapes."""

import json
import secrets
import time

from .base import (
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    split_composite_id,
)
from .github import GitHubProvider, parse_issue_comment


class FakeGitHubProvider:
    name = "fake-github"
    channel = "github"
    capabilities = GitHubProvider.capabilities
    connect_credentials = ()
    oauth = True
    client_id = "123456"

    def __init__(self, app_slug: str = "caspian-test") -> None:
        self.app_id = self.client_id
        self.app_slug = app_slug
        self.installation_id = str(9_000_000 + secrets.randbelow(1_000_000))
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self.refreshes = 0
        self._seq = 1000

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        return GitHubProvider.route_key(payload)

    def authorize_url(self, redirect_uri: str, state: str, app=None) -> str:
        return f"https://github.com/apps/{self.app_slug}/installations/new?state={state}"

    def exchange_installation(self, installation_id: str, app=None) -> dict:
        self.installation_id = str(installation_id)
        return {
            "credentials": {
                "github_app_id": self.app_id,
                "github_app_slug": self.app_slug,
                "github_private_key": "fake-private-key",
                "github_webhook_secret": "fake-webhook-secret",
                "installation_id": self.installation_id,
                "installation_token": f"ghs_fake_{installation_id}",
                "token_expires_at": int(time.time()) + 3600,
            },
            "provider_resource_id": self.installation_id,
            "address": "github:acme",
        }

    @staticmethod
    def needs_refresh(credentials) -> bool:
        return GitHubProvider.needs_refresh(credentials)

    def refresh_credentials(self, credentials) -> dict:
        self.refreshes += 1
        return {
            **credentials,
            "installation_token": f"ghs_fake_refreshed_{self.refreshes}",
            "token_expires_at": int(time.time()) + 3600,
        }

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = request.credentials or {}
        return ProvisionResult(
            address=credentials.get("address", "github:acme"),
            provider_resource_id=credentials.get("installation_id", self.installation_id),
        )

    def _result(self, thread_id: str, target: list[dict], text: str | None) -> SendResult:
        self._seq += 1
        target.append({"thread_id": thread_id, "text": text})
        return SendResult(
            provider_message_id=f"{thread_id}:{self._seq}",
            provider_thread_id=thread_id,
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        if not message.to:
            raise ValueError("GitHub send requires owner/repo#issue_number in message.to")
        return self._result(message.to[0], self.sent, message.text)

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        thread_id, _ = split_composite_id(provider_message_id)
        return self._result(thread_id, self.replies, message.text)

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        event = {key.lower(): value for key, value in headers.items()}.get(
            "x-github-event", "issue_comment"
        )
        if event != "issue_comment":
            return []
        credentials = credentials or {}
        return parse_issue_comment(
            data,
            delivery_id={key.lower(): value for key, value in headers.items()}.get(
                "x-github-delivery", ""
            ),
            app_slug=credentials.get("github_app_slug", self.app_slug),
            receive_mode=credentials.get("receive_mode", "mentions"),
        )

    def webhook_payload(
        self,
        *,
        repository: str = "acme/widget",
        issue_number: int = 42,
        comment_id: int | None = None,
        user: str = "octocat",
        text: str | None = None,
        pull_request: bool = False,
        user_type: str = "User",
        action: str = "created",
    ) -> dict:
        self._seq += 1
        issue: dict = {"number": issue_number}
        if pull_request:
            issue["pull_request"] = {"url": f"https://api.github.com/repos/{repository}/pulls/42"}
        return {
            "action": action,
            "installation": {"id": int(self.installation_id)},
            "repository": {"full_name": repository},
            "issue": issue,
            "comment": {
                "id": comment_id or self._seq,
                "body": text if text is not None else f"@{self.app_slug} please help",
                "user": {"login": user, "type": user_type},
            },
        }
