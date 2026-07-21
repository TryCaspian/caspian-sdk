"""GitHub App adapter for issue and pull-request conversation comments.

GitHub delivers ``issue_comment`` events to an App webhook. Issues and pull
requests share the issue-comments REST API, so both normalize to one Caspian
conversation whose id is ``owner/repo#number``. The installation id routes each
delivery to the connection that owns it.

Only comments that mention the App are delivered by default. Set the stored
connection credential ``receive_mode`` to ``all`` to receive every human
comment from repositories selected during installation.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

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

API = "https://api.github.com"
API_VERSION = "2022-11-28"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _private_key(value: str) -> bytes:
    """Return an inline PEM value or read one from a configured path."""
    if value and "BEGIN" not in value:
        return Path(value).read_bytes()
    return value.encode()


def app_jwt(app_id: str, private_key: str, now: int | None = None) -> str:
    """Create the short-lived RS256 JWT GitHub uses to authenticate an App."""
    issued_at = int(now or time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64url(
        json.dumps(
            {"iat": issued_at - 60, "exp": issued_at + 9 * 60, "iss": str(app_id)},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(_private_key(private_key), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(signature)}"


def parse_issue_comment(
    data: dict,
    *,
    delivery_id: str = "",
    app_slug: str = "",
    receive_mode: str = "mentions",
) -> list[InboundMessage]:
    """Normalize a created ``issue_comment`` webhook into Caspian's schema."""
    if data.get("action") != "created":
        return []
    comment = data.get("comment") or {}
    sender = comment.get("user") or {}
    body = comment.get("body")
    if not body or sender.get("type") == "Bot":
        return []
    if receive_mode != "all":
        if not app_slug:
            return []
        mention = re.compile(rf"(?<![\w-])@{re.escape(app_slug)}(?![\w-])", re.IGNORECASE)
        if not mention.search(body):
            return []

    installation_id = str((data.get("installation") or {}).get("id", ""))
    repository = data.get("repository") or {}
    full_name = repository.get("full_name", "")
    issue = data.get("issue") or {}
    number = issue.get("number")
    comment_id = comment.get("id")
    if not installation_id or not full_name or number is None or comment_id is None:
        return []

    thread_id = f"{full_name}#{number}"
    return [
        InboundMessage(
            external_event_id=delivery_id or f"github:{comment_id}",
            provider_inbox_id=installation_id,
            provider_message_id=f"{thread_id}:{comment_id}",
            provider_thread_id=thread_id,
            sender_address=sender.get("login"),
            sender_name=sender.get("name") or sender.get("login"),
            text=body,
            chat_type="pull_request" if issue.get("pull_request") else "issue",
        )
    ]


class GitHubProvider:
    name = "github"
    channel = "github"
    connect_credentials = ()
    oauth = True
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        app_id: str = "",
        app_slug: str = "",
        private_key: str = "",
        webhook_secret: str = "",
        base_url: str = API,
    ) -> None:
        self.app_id = str(app_id)
        self.app_slug = app_slug
        self.private_key = private_key
        self.webhook_secret = webhook_secret
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)

    @property
    def client_id(self) -> str:
        """Compatibility marker used by gateways to detect a shared App."""
        return self.app_id

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        """Route an untrusted delivery by installation id before verification."""
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        installation_id = (data.get("installation") or {}).get("id")
        return str(installation_id) if installation_id is not None else None

    def _app(self, credentials: Mapping[str, str] | None) -> tuple[str, str, str, str]:
        credentials = credentials or {}
        return (
            str(credentials.get("github_app_id") or self.app_id),
            credentials.get("github_app_slug") or self.app_slug,
            credentials.get("github_private_key") or self.private_key,
            credentials.get("github_webhook_secret") or self.webhook_secret,
        )

    def authorize_url(
        self, redirect_uri: str, state: str, app: Mapping[str, str] | None = None
    ) -> str:
        """Return the App installation URL.

        ``redirect_uri`` is configured as the GitHub App's Setup URL rather than
        passed to GitHub. GitHub redirects there with ``installation_id`` and
        preserves ``state`` from this URL.
        """
        _, slug, _, _ = self._app(app)
        if not slug:
            raise ValueError("GitHub App slug is required")
        return f"https://github.com/apps/{slug}/installations/new?{urlencode({'state': state})}"

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _mint_token(
        self, installation_id: str, app_credentials: Mapping[str, str] | None = None
    ) -> dict:
        app_id, _, private_key, _ = self._app(app_credentials)
        if not app_id or not private_key:
            raise ValueError("GitHub App id and private key are required")
        jwt = app_jwt(app_id, private_key)
        response = self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers=self._headers(jwt),
        )
        response.raise_for_status()
        data = response.json()
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        return {
            "installation_token": data["token"],
            "token_expires_at": int(expires_at.timestamp()),
        }

    def exchange_installation(
        self, installation_id: str, app: Mapping[str, str] | None = None
    ) -> dict:
        """Complete a GitHub App installation and return connection metadata."""
        app_id, slug, private_key, webhook_secret = self._app(app)
        jwt = app_jwt(app_id, private_key)
        response = self._client.get(
            f"/app/installations/{installation_id}",
            headers=self._headers(jwt),
        )
        response.raise_for_status()
        installation = response.json()
        account = installation.get("account") or {}
        credentials = {
            "github_app_id": app_id,
            "github_app_slug": slug,
            "github_private_key": private_key,
            "github_webhook_secret": webhook_secret,
            "installation_id": str(installation_id),
            **self._mint_token(str(installation_id), app),
        }
        return {
            "credentials": credentials,
            "provider_resource_id": str(installation_id),
            "address": f"github:{account.get('login') or installation_id}",
        }

    @staticmethod
    def needs_refresh(credentials: Mapping[str, str] | None) -> bool:
        credentials = credentials or {}
        expires_at = credentials.get("token_expires_at")
        return not credentials.get("installation_token") or (
            expires_at is not None and time.time() >= int(expires_at) - 120
        )

    def refresh_credentials(self, credentials: Mapping[str, str]) -> dict:
        refreshed = self._mint_token(credentials["installation_id"], credentials)
        return {**credentials, **refreshed}

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = request.credentials or {}
        return ProvisionResult(
            address=credentials.get("address", "github"),
            provider_resource_id=credentials.get(
                "provider_resource_id", credentials.get("installation_id", "")
            ),
        )

    @staticmethod
    def _thread_parts(thread_id: str) -> tuple[str, str, str]:
        repository, separator, number = thread_id.rpartition("#")
        owner, slash, repo = repository.partition("/")
        if not separator or not slash or not owner or not repo or not number.isdigit():
            raise ValueError("GitHub destination must be owner/repo#issue_number")
        return owner, repo, number

    def _post_comment(
        self, thread_id: str, message: OutboundMessage, credentials: Mapping[str, str] | None
    ) -> SendResult:
        credentials = credentials or {}
        owner, repo, number = self._thread_parts(thread_id)
        response = self._client.post(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": message.text or ""},
            headers=self._headers(credentials["installation_token"]),
        )
        response.raise_for_status()
        comment = response.json()
        return SendResult(
            provider_message_id=f"{thread_id}:{comment['id']}",
            provider_thread_id=thread_id,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        if not message.to:
            raise ValueError("GitHub send requires owner/repo#issue_number in message.to")
        return self._post_comment(message.to[0], message, credentials)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        thread_id, _ = split_composite_id(provider_message_id)
        return self._post_comment(thread_id, message, credentials)

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        header_map = lower_headers(headers)
        _, app_slug, _, webhook_secret = self._app(credentials)
        if not webhook_secret:
            raise WebhookVerificationError("GitHub webhook secret is not configured")
        expected = "sha256=" + hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, header_map.get("x-hub-signature-256", "")):
            raise WebhookVerificationError("GitHub signature mismatch")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        event = header_map.get("x-github-event", "")
        if event in {"ping", "installation"}:
            return []
        if event != "issue_comment":
            return []
        receive_mode = (credentials or {}).get("receive_mode", "mentions")
        return parse_issue_comment(
            data,
            delivery_id=header_map.get("x-github-delivery", ""),
            app_slug=app_slug,
            receive_mode=receive_mode,
        )
