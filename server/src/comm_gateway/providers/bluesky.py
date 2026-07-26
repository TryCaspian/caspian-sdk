import datetime
import json
import logging
from collections.abc import Mapping

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


def _get_utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class BlueskyProvider:
    name = "bluesky"
    channel = "bluesky"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    connect_credentials: tuple[str, ...] = ("identifier", "password")

    def __init__(
        self,
        identifier: str = "",
        password: str = "",
        base_url: str = "https://bsky.social",
    ) -> None:
        self._default_identifier = identifier
        self._default_password = password
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=30.0)

    def _get_credentials(self, credentials: Mapping[str, str] | None) -> tuple[str, str]:
        creds = credentials or {}
        identifier = creds.get("identifier") or self._default_identifier
        password = creds.get("password") or self._default_password
        if not identifier or not password:
            raise ValueError("bluesky needs an identifier and app password")
        return identifier, password

    def _get_session(self, identifier: str, password: str) -> dict:
        r = self._client.post(
            "/xrpc/com.atproto.server.createSession",
            json={"identifier": identifier, "password": password},
        )
        r.raise_for_status()
        return r.json()

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        identifier, password = self._get_credentials(request.credentials)
        session = self._get_session(identifier, password)
        return ProvisionResult(
            address=session["handle"],
            provider_resource_id=session["did"],
        )

    def poll_dms(
        self, credentials: Mapping[str, str] | None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str]:
        identifier, password = self._get_credentials(credentials)
        session = self._get_session(identifier, password)
        headers = {"Authorization": f"Bearer {session['accessJwt']}"}
        
        params = {"limit": 50}
        
        r = self._client.get("/xrpc/app.bsky.notification.listNotifications", headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        notifications = data.get("notifications", [])
        
        out: list[InboundMessage] = []
        newest = cursor

        for notif in notifications:
            indexed_at = notif.get("indexedAt")
            if newest is None or indexed_at > newest:
                newest = indexed_at
            
            if cursor is not None and indexed_at <= cursor:
                continue

            reason = notif.get("reason")
            if reason not in ("mention", "reply"):
                continue

            author = notif.get("author", {})
            did = author.get("did")
            handle = author.get("handle")
            uri = notif.get("uri")
            cid = notif.get("cid")
            record = notif.get("record", {})
            text = record.get("text", "")
            root_uri = uri
            root_cid = cid
            if "reply" in record and "root" in record["reply"]:
                root_uri = record["reply"]["root"].get("uri", uri)
                root_cid = record["reply"]["root"].get("cid", cid)

            provider_message_id = f"{uri}|{cid}|{root_uri}|{root_cid}"

            out.append(
                InboundMessage(
                    external_event_id=uri,
                    provider_inbox_id=session["did"],
                    provider_message_id=provider_message_id,
                    provider_thread_id=root_uri,
                    sender_address=did,
                    sender_name=handle,
                    recipients=[{"address": session["did"]}],
                    text=text,
                    chat_type="bluesky",
                )
            )

        if cursor is None:
            return [], newest or "1970-01-01T00:00:00.000Z"
        out.reverse()
        return out, newest or cursor

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        identifier, password = self._get_credentials(credentials)
        session = self._get_session(identifier, password)
        headers = {"Authorization": f"Bearer {session['accessJwt']}"}

        record = {
            "$type": "app.bsky.feed.post",
            "text": message.text or "",
            "createdAt": _get_utc_now_iso(),
        }

        body = {
            "repo": session["did"],
            "collection": "app.bsky.feed.post",
            "record": record,
        }

        r = self._client.post("/xrpc/com.atproto.repo.createRecord", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        uri = data["uri"]
        cid = data["cid"]
        
        provider_message_id = f"{uri}|{cid}|{uri}|{cid}"
        return SendResult(provider_message_id=provider_message_id, provider_thread_id=uri)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        identifier, password = self._get_credentials(credentials)
        session = self._get_session(identifier, password)
        headers = {"Authorization": f"Bearer {session['accessJwt']}"}
        parts = provider_message_id.split("|")
        parent_uri = parts[0]
        parent_cid = parts[1] if len(parts) > 1 else ""
        root_uri = parts[2] if len(parts) > 2 else parent_uri
        root_cid = parts[3] if len(parts) > 3 else parent_cid

        record = {
            "$type": "app.bsky.feed.post",
            "text": message.text or "",
            "createdAt": _get_utc_now_iso(),
            "reply": {
                "root": {"uri": root_uri, "cid": root_cid},
                "parent": {"uri": parent_uri, "cid": parent_cid},
            }
        }

        body = {
            "repo": session["did"],
            "collection": "app.bsky.feed.post",
            "record": record,
        }

        r = self._client.post("/xrpc/com.atproto.repo.createRecord", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        uri = data["uri"]
        cid = data["cid"]
        
        new_message_id = f"{uri}|{cid}|{root_uri}|{root_cid}"
        return SendResult(provider_message_id=new_message_id, provider_thread_id=root_uri)

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        return []
