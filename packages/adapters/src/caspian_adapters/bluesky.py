"""Bluesky adapter: AT Protocol polling + posting."""

import datetime
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

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

log = logging.getLogger("comm.bluesky")

X_BLUESKY_TOKEN_HEADER = "x-bluesky-token"


def parse_notification(notif: dict, account_did: str) -> InboundMessage | None:
    """Normalize a Bluesky listNotifications item into an InboundMessage.

    Only processes "mention" and "reply" notifications. Skips own notifications.
    """
    reason = notif.get("reason")
    if reason not in {"mention", "reply"}:
        return None

    author = notif.get("author", {})
    sender_did = author.get("did")
    if not sender_did or sender_did == account_did:
        return None

    record = notif.get("record", {})
    text = record.get("text")
    if text is None:
        return None

    uri = notif.get("uri", "")
    indexed_at = notif.get("indexedAt", "")

    # Extract thread ID from reply structure, or use post URI if it's not a reply
    reply_ref = record.get("reply", {})
    root_ref = reply_ref.get("root", {})
    root_uri = root_ref.get("uri") or uri

    return InboundMessage(
        external_event_id=f"{uri}:{indexed_at}",
        provider_inbox_id=account_did,
        provider_message_id=uri,
        provider_thread_id=root_uri,
        sender_address=sender_did,
        sender_name=author.get("displayName") or author.get("handle") or sender_did,
        text=text,
        chat_type=f"bluesky_{reason}",
    )


class BlueskyProvider:
    name = "bluesky"
    channel = "bluesky"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    connect_credentials = ("handle", "app_password")

    def __init__(
        self,
        handle: str = "",
        app_password: str = "",
        webhook_token: str = "",
        base_url: str = "https://bsky.social",
        poll_interval: float = 15.0,
    ) -> None:
        self._default_handle = handle
        self._default_app_password = app_password
        self._webhook_token = webhook_token
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._client = httpx.Client(base_url=self._base_url, timeout=30.0)

    def _get_creds(self, credentials: Mapping[str, str] | None) -> tuple[str, str]:
        creds = credentials or {}
        handle = creds.get("handle") or self._default_handlepython 
        password = creds.get("app_password") or self._default_app_password
        if not handle or not password:
            raise ValueError(
                "bluesky needs a handle and app_password "
                "(per-connection credentials or COMM_BLUESKY_HANDLE/APP_PASSWORD fallback)"
            )
        return handle, password

    def _create_session(self, handle: str, password: str) -> dict[str, Any]:
        """Call com.atproto.server.createSession."""
        resp = self._client.post(
            "/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": password},
        )
        resp.raise_for_status()
        return resp.json()

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        handle, password = self._get_creds(request.credentials)
        session = self._create_session(handle, password)
        did = session.get("did")
        if not did:
            raise ValueError("bluesky provision failed: did not returned in session")
        # address is the display handle, resource_id is the DID
        return ProvisionResult(address=f"@{handle}", provider_resource_id=did)

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        handle, password = self._get_creds(credentials)
        session = self._create_session(handle, password)
        token = session["accessJwt"]
        did = session["did"]

        now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": message.text or "",
            "createdAt": now,
        }

        resp = self._client.post(
            "/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": record,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        uri = data["uri"]
        return SendResult(provider_message_id=uri, provider_thread_id=uri)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        handle, password = self._get_creds(credentials)
        session = self._create_session(handle, password)
        token = session["accessJwt"]
        did = session["did"]

        # To reply, we need the CID of the target post and its root.
        posts_resp = self._client.get(
            "/xrpc/app.bsky.feed.getPosts",
            params={"uris": provider_message_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        posts_resp.raise_for_status()
        posts_data = posts_resp.json()
        posts = posts_data.get("posts", [])
        if not posts:
            raise ValueError(f"Target post not found: {provider_message_id}")

        target_post = posts[0]
        target_cid = target_post["cid"]
        target_uri = target_post["uri"]

        # Determine root
        record = target_post.get("record", {})
        reply_ref = record.get("reply")
        if reply_ref:
            root_uri = reply_ref["root"]["uri"]
            root_cid = reply_ref["root"]["cid"]
        else:
            root_uri = target_uri
            root_cid = target_cid

        now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
        new_record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": message.text or "",
            "createdAt": now,
            "reply": {
                "root": {"uri": root_uri, "cid": root_cid},
                "parent": {"uri": target_uri, "cid": target_cid},
            },
        }

        resp = self._client.post(
            "/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": new_record,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        new_uri = data["uri"]
        return SendResult(provider_message_id=new_uri, provider_thread_id=root_uri)

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        if self._webhook_token:
            received = lower_headers(headers).get(X_BLUESKY_TOKEN_HEADER, "")
            if not hmac.compare_digest(received, self._webhook_token):
                raise WebhookVerificationError("Bluesky webhook token mismatch")

        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc

        account_did = (credentials or {}).get("provider_resource_id")
        if not account_did:
            handle, password = self._get_creds(credentials)
            session = self._create_session(handle, password)
            account_did = session["did"]

        if isinstance(data, list):
            notifs = data
        elif "notifications" in data:
            notifs = data["notifications"]
        else:
            notifs = [data]

        out = []
        for n in notifs:
            msg = parse_notification(n, account_did)
            if msg:
                out.append(msg)
        return out

    def poll_notifications(
        self,
        credentials: Mapping[str, str] | None = None,
        cursor: str | None = None,
    ) -> tuple[list[InboundMessage], str]:
        """Poll app.bsky.notification.listNotifications."""
        handle, password = self._get_creds(credentials)
        session = self._create_session(handle, password)
        token = session["accessJwt"]
        did = session["did"]

        params = {"limit": "50"}
        resp = self._client.get(
            "/xrpc/app.bsky.notification.listNotifications",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        notifs = data.get("notifications", [])

        newest = cursor
        for n in notifs:
            idx = n.get("indexedAt")
            if idx and (newest is None or idx > newest):
                newest = idx

        if cursor is None:
            # First poll, baseline to avoid fetching all history
            return [], newest or "0"

        fresh = []
        for n in notifs:
            msg = parse_notification(n, did)
            if msg:
                idx = n.get("indexedAt")
                if idx and idx > cursor:
                    fresh.append((idx, msg))

        # listNotifications returns newest first.
        # Sort oldest first so they are processed in chronological order.
        fresh.sort(key=lambda x: x[0])
        return [msg for _, msg in fresh], newest or cursor
