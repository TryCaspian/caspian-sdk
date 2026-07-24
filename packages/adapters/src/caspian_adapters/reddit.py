"""Reddit adapter (OAuth per user, inbox polling)."""

import time
from collections.abc import Mapping
from typing import Any

import httpx

from .base import Capability, InboundMessage, OutboundMessage, SendResult


def _parse_message(data: Mapping[str, Any], provider_inbox_id: str) -> InboundMessage | None:
    name = data.get("name")
    if not name:
        return None

    author = data.get("author")
    auto_generated = not bool(author)

    return InboundMessage(
        external_event_id=name,
        provider_inbox_id=provider_inbox_id,
        provider_message_id=name,
        provider_thread_id=data.get("first_message_name") or name,
        sender_address=author or None,
        sender_name=author or None,
        subject=data.get("subject", ""),
        text=data.get("body", ""),
        chat_type="private",
        edited=False,
        attachments=[],
        recipients=[],
        auto_generated=auto_generated,
    )


def parse_inbox_response(
    payload: Mapping[str, Any],
    *,
    provider_inbox_id: str,
) -> list[InboundMessage]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []

    children = data.get("children")
    if not isinstance(children, list):
        return []

    out: list[InboundMessage] = []
    for child in children:
        if not isinstance(child, Mapping) or child.get("kind") != "t4":
            continue

        msg_data = child.get("data")
        if not isinstance(msg_data, Mapping):
            continue

        msg = _parse_message(msg_data, provider_inbox_id)
        if msg is not None:
            out.append(msg)

    return out


class RedditProvider:
    name = "reddit"
    channel = "reddit"
    oauth = True
    connect_credentials = ()
    capabilities = frozenset(
        {Capability.RECEIVE, Capability.REPLY, Capability.SEND}
    )

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = httpx.Client(timeout=30.0)

    def _call(
        self,
        token: str,
        method: str,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        kwargs = {}
        if data is not None:
            kwargs["data"] = data
        if params is not None:
            kwargs["params"] = params

        response = self._client.request(
            method,
            f"https://oauth.reddit.com{endpoint}",
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": "caspian-sdk/1.0",
            },
            **kwargs,
        )
        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):
            # Reddit sometimes returns application errors with HTTP 200
            json_data = data.get("json")
            if isinstance(json_data, dict):
                errors = json_data.get("errors")
                if errors:
                    raise RuntimeError(f"Reddit API error: {errors}")

            if "error" in data and isinstance(data["error"], (str, int)):
                msg = data.get("message") or data["error"]
                raise RuntimeError(f"Reddit API error: {msg}")

        return data

    def needs_refresh(self, credentials: Mapping[str, str] | None) -> bool:
        creds = credentials or {}
        if not creds.get("refresh_token") or not creds.get("token_expires_at"):
            return False
        return time.time() >= int(creds["token_expires_at"]) - 120

    def refresh_credentials(self, credentials: Mapping[str, str]) -> dict:
        client_id = credentials.get("client_id") or self.client_id
        client_secret = credentials.get("client_secret") or self.client_secret

        r = self._client.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
            },
        )
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(f"Reddit token refresh failed: {data.get('error')}")

        out = dict(credentials)
        out["bot_token"] = data["access_token"]
        out["token_expires_at"] = int(time.time()) + int(data.get("expires_in", 3600))
        return out

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        token = (credentials or {}).get("bot_token", "")
        data = {
            "api_type": "json",
            "to": message.to[0],
            "subject": message.subject or "(No Subject)",
            "text": message.text or "",
        }
        self._call(token, "POST", "/api/compose", data=data)
        
        return SendResult(
            provider_message_id="",
            provider_thread_id=message.to[0],
        )

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult:
        token = (credentials or {}).get("bot_token", "")
        data = {
            "api_type": "json",
            "thing_id": provider_message_id,
            "text": message.text or "",
        }
        resp = self._call(token, "POST", "/api/comment", data=data)
        
        provider_msg_id = ""
        try:
            provider_msg_id = resp["json"]["data"]["things"][0]["data"]["name"]
        except (KeyError, IndexError, TypeError):
            pass

        return SendResult(
            provider_message_id=provider_msg_id,
            provider_thread_id=provider_message_id,
        )
