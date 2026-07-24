"""Bluesky (AT Protocol) adapter: mentions and replies.

Polling based. The agent authenticates via `com.atproto.server.createSession`
with a handle and app password.
Outbound creates posts via `com.atproto.repo.createRecord`.
Inbound polls `app.bsky.notification.listNotifications`.
"""

import datetime
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
)

log = logging.getLogger("comm.bluesky")


class BlueskyProvider:
    name = "bluesky"
    channel = "bluesky"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    # Polling adapter; credentials provided via config/connect
    connect_credentials = ("handle", "app_password")

    def __init__(
        self, handle: str = "", app_password: str = "", base_url: str = "https://bsky.social"
    ):
        self.handle = handle
        self.app_password = app_password
        self.base_url = base_url.rstrip("/")

    def _auth(self, client: httpx.Client, credentials: Mapping[str, str] | None) -> str:
        creds = credentials or {}
        handle = creds.get("handle") or self.handle
        app_password = creds.get("app_password") or self.app_password

        resp = client.post(
            f"{self.base_url}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
        )
        resp.raise_for_status()
        return resp.json()["accessJwt"]

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials
        handle = creds.get("handle") or self.handle
        app_password = creds.get("app_password") or self.app_password

        with httpx.Client() as client:
            resp = client.post(
                f"{self.base_url}/xrpc/com.atproto.server.createSession",
                json={"identifier": handle, "password": app_password},
            )
            if resp.status_code >= 400:
                raise ValueError("Bluesky auth failed: " + resp.text)
            did = resp.json()["did"]
        return ProvisionResult(
            address=handle,
            provider_resource_id=did,
        )

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        text = message.text or ""
        with httpx.Client() as client:
            token = self._auth(client, credentials)

            # Simplified text-only post for standard send
            now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
            record = {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": now,
            }

            resp = client.post(
                f"{self.base_url}/xrpc/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "repo": provider_inbox_id,
                    "collection": "app.bsky.feed.post",
                    "record": record,
                },
            )
            resp.raise_for_status()
            uri = resp.json()["uri"]
            return SendResult(provider_message_id=uri)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        text = message.text or ""
        with httpx.Client() as client:
            token = self._auth(client, credentials)

            # Need the parent post's CID to reply properly
            # In a real implementation we'd fetch the parent record, but for this
            # adapter we assume provider_message_id is composite "uri:cid" or we just
            # fetch the record dynamically if it's just a URI.

            # For simplicity in this challenge, if we just have a URI, we need its CID.
            # Let's extract CID by fetching the record if it isn't composite.
            uri, _, _ = provider_message_id.partition("||")
            p_resp = client.get(
                f"{self.base_url}/xrpc/app.bsky.feed.getPosts",
                headers={"Authorization": f"Bearer {token}"},
                params={"uris": [uri]},
            )
            p_resp.raise_for_status()
            posts = p_resp.json().get("posts", [])
            if not posts:
                raise ValueError("Parent post not found")
            parent = posts[0]
            cid = parent["cid"]
            root = parent.get("record", {}).get("reply", {}).get("root")
            root_uri = root["uri"] if root else uri
            root_cid = root["cid"] if root else cid

            now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
            record = {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": now,
                "reply": {
                    "root": {"uri": root_uri, "cid": root_cid},
                    "parent": {"uri": uri, "cid": cid},
                },
            }

            resp = client.post(
                f"{self.base_url}/xrpc/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "repo": provider_inbox_id,
                    "collection": "app.bsky.feed.post",
                    "record": record,
                },
            )
            resp.raise_for_status()
            out_uri = resp.json()["uri"]
            return SendResult(provider_message_id=out_uri)

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]:
        # Polling adapter; does not receive inbound webhooks
        return []

    def poll_mentions(
        self, credentials: Mapping[str, str] | None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str | None]:
        creds = credentials or {}
        handle = creds.get("handle") or self.handle

        with httpx.Client() as client:
            token = self._auth(client, credentials)

            params: dict[str, Any] = {"limit": 50}

            resp = client.get(
                f"{self.base_url}/xrpc/app.bsky.notification.listNotifications",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            notifications = data.get("notifications", [])
            new_cursor = notifications[0]["uri"] if notifications else cursor

            new_notifs = []
            for notif in notifications:
                if notif["uri"] == cursor:
                    break
                new_notifs.append(notif)

            messages = []
            # We must process oldest-first for causality
            for notif in reversed(new_notifs):
                reason = notif.get("reason")
                if reason not in ("mention", "reply"):
                    continue

                uri = notif["uri"]
                cid = notif["cid"]
                author = notif.get("author", {})
                sender_did = author.get("did")
                sender_handle = author.get("handle")

                record = notif.get("record", {})
                text = record.get("text", "")

                # provider_thread_id is typically the root URI for threads
                reply_ref = record.get("reply", {})
                root = reply_ref.get("root", {})
                thread_id = root.get("uri") or uri

                msg = InboundMessage(
                    external_event_id=uri,
                    provider_inbox_id=handle,  # our handle is the inbox
                    provider_message_id=f"{uri}||{cid}",
                    provider_thread_id=thread_id,
                    sender_address=sender_did,
                    sender_name=sender_handle,
                    text=text,
                    chat_type="public",
                )
                messages.append(msg)

            return messages, new_cursor
