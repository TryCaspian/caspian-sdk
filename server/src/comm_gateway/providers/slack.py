"""Slack adapter (OAuth per workspace, Events API inbound).

One Slack app, installed into each workspace via OAuth ("Add to Slack"). The
install grants a per-workspace bot token, stored on the connection. Inbound is
the Events API: Slack POSTs signed events to the scoped webhook. Outbound is
chat.postMessage. provider_thread_id is the Slack channel id;
provider_message_id is "{channel}:{ts}" so a reply threads on the message ts.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping

import httpx

from . import blocks as blocks_render
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

API = "https://slack.com/api"

# Reject requests whose signed timestamp is older than this, so a captured
# request can't be replayed indefinitely (matches Slack's own guidance).
MAX_TIMESTAMP_SKEW = 60 * 5


def _interaction_payload(body: bytes) -> dict | None:
    """Slack interactivity POSTs are `application/x-www-form-urlencoded` with a
    single `payload` field holding the JSON. Pull it out (returns None if absent)."""
    from urllib.parse import parse_qs

    try:
        form = parse_qs(body.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    raw = form.get("payload", [None])[0]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _media_blocks(media) -> list[dict]:
    """Render outbound attachments as Block Kit image blocks (image mime types).
    Non-image files are surfaced as a link section so the URL still reaches the
    channel — native file upload (files.upload) is a live-testing follow-up."""
    out: list[dict] = []
    for item in media or []:
        url = item.get("url")
        if not url:
            continue
        mime = str(item.get("mime_type") or "")
        if mime.startswith("image/"):
            out.append({"type": "image", "image_url": url,
                        "alt_text": item.get("name") or "image"})
        else:
            name = item.get("name") or url
            out.append({"type": "section",
                        "text": {"type": "mrkdwn", "text": f"<{url}|{name}>"}})
    return out


def _inbox_id(data: dict) -> str:
    # Route by (app, workspace) = api_app_id:team_id. The pool means several apps
    # can live in one workspace, so the app id disambiguates which developer's
    # connection this event belongs to.
    return f"{data.get('api_app_id', '')}:{data.get('team_id', '')}"


def parse_event(data: dict) -> list[InboundMessage]:
    """Normalize a Slack Events API callback into our schema.

    User messages become message.received; reaction_added/removed become
    reaction.received. Bot messages and message subtypes are ignored."""
    event = data.get("event", {})
    event_type = event.get("type")
    if event_type in ("reaction_added", "reaction_removed"):
        item = event.get("item") or {}
        if item.get("type") != "message":
            return []  # reactions on files/other items aren't ours to route
        channel = item.get("channel", "")
        ts = item.get("ts", "")
        return [
            InboundMessage(
                kind="reaction",
                external_event_id=(data.get("event_id")
                                   or f"react:{channel}:{ts}:{event.get('event_ts')}"),
                provider_inbox_id=_inbox_id(data),
                provider_message_id=f"{channel}:{ts}",
                provider_thread_id=channel,
                sender_address=event.get("user"),
                reaction={
                    "emoji": event.get("reaction"),
                    "action": "added" if event_type == "reaction_added" else "removed",
                    "source_message_id": f"{channel}:{ts}",
                },
            )
        ]
    if event_type != "message" or event.get("bot_id") or event.get("subtype"):
        return []
    channel = event["channel"]
    ts = event["ts"]
    return [
        InboundMessage(
            external_event_id=data.get("event_id") or f"{channel}:{ts}",
            provider_inbox_id=_inbox_id(data),
            provider_message_id=f"{channel}:{ts}",
            provider_thread_id=channel,
            sender_address=event.get("user"),
            text=event.get("text"),
            chat_type=event.get("channel_type") or "channel",
        )
    ]


def parse_block_actions(payload: dict) -> list[InboundMessage]:
    """Normalize a Slack `block_actions` interactivity payload into an interaction.

    Slack posts these to the app's Interactivity Request URL when a user taps a
    button. We read the first action's `value` (the agent's callback value, set in
    blocks.to_slack) and the message the button lived on."""
    if payload.get("type") != "block_actions":
        return []
    actions = payload.get("actions") or []
    if not actions:
        return []
    action = actions[0]
    channel = (payload.get("channel") or {}).get("id", "")
    message = payload.get("message") or {}
    ts = message.get("ts", "")
    source_id = f"{channel}:{ts}" if channel and ts else None
    user = payload.get("user") or {}
    app_id = payload.get("api_app_id", "")
    team = (payload.get("team") or {}).get("id", "") or payload.get("team_id", "")
    action_ts = action.get("action_ts") or payload.get("trigger_id") or action.get("action_id")
    return [
        InboundMessage(
            kind="interaction",
            external_event_id=f"{app_id}:{action.get('action_id')}:{action_ts}",
            provider_inbox_id=f"{app_id}:{team}",
            provider_message_id=source_id or f"{app_id}:{action_ts}",
            provider_thread_id=channel,
            sender_address=user.get("id"),
            sender_name=user.get("username") or user.get("name"),
            action={"value": action.get("value"), "source_message_id": source_id},
        )
    ]


class SlackProvider:
    name = "slack"
    channel = "slack"
    # Installed via OAuth, so the token is supplied by the callback, not the
    # connect body; connect_credentials is empty and the connection starts
    # pending until the workspace owner approves the install.
    connect_credentials = ()
    oauth = True
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INTERACTIONS,
            Capability.REACTIONS,
            Capability.MEDIA,
        }
    )

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        signing_secret: str = "",
        scopes: str = ("chat:write,chat:write.customize,channels:history,"
                       "im:history,app_mentions:read"),
        base_url: str = API,
        apps: list[dict] | None = None,
    ) -> None:
        # A POOL of shared apps. Two developers' agents can't share one Slack app
        # in the same workspace (Slack allows one install of an app per workspace),
        # so we keep several interchangeable apps and hand each colliding developer
        # a different one. `apps` is the pool; the single client_id/... args are the
        # 1-app fallback (bring-your-own or a single shared app).
        if apps:
            self.apps = [dict(a) for a in apps]
        elif client_id:
            self.apps = [{"app_id": "", "client_id": client_id,
                          "client_secret": client_secret, "signing_secret": signing_secret}]
        else:
            self.apps = []
        self.scopes = scopes
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    @property
    def client_id(self) -> str:
        """First pool app's client id - non-empty means a shared app is configured."""
        return self.apps[0]["client_id"] if self.apps else ""

    def pool_size(self) -> int:
        return len(self.apps)

    def app_at(self, index: int) -> dict:
        """The pool app at `index` (wraps to the last if out of range but non-empty)."""
        if not self.apps:
            return {}
        return self.apps[min(index, len(self.apps) - 1)]

    def _app_by_id(self, app_id: str) -> dict:
        """Find the pool app matching a Slack api_app_id (falls back to the first)."""
        for a in self.apps:
            if a.get("app_id") and a["app_id"] == app_id:
                return a
        return self.apps[0] if self.apps else {}

    # OAuth

    def _app(self, app: Mapping[str, str] | None) -> tuple[str, str, str]:
        """The Slack app credentials to use: the connection's own (bring-your-own,
        or the pool app pinned at install) if present, else the first pool app."""
        app = app or {}
        first = self.apps[0] if self.apps else {}
        return (
            app.get("slack_client_id") or first.get("client_id", ""),
            app.get("slack_client_secret") or first.get("client_secret", ""),
            app.get("slack_signing_secret") or first.get("signing_secret", ""),
        )

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        """Route inbound by (app, workspace): api_app_id:team_id. The pool means many
        apps can be installed in one workspace, so the workspace alone isn't unique -
        the app id disambiguates which developer's connection this event belongs to."""
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        team = data.get("team_id", "")
        app_id = data.get("api_app_id", "")
        if not (team or app_id):
            return None
        return f"{app_id}:{team}"

    def authorize_url(
        self, redirect_uri: str, state: str, app: Mapping[str, str] | None = None
    ) -> str:
        from urllib.parse import urlencode

        client_id, _, _ = self._app(app)
        q = urlencode(
            {
                "client_id": client_id,
                "scope": self.scopes,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"https://slack.com/oauth/v2/authorize?{q}"

    def _oauth_credentials(self, data: dict) -> dict:
        """Build the stored credentials from an oauth.v2.access response.

        With token rotation enabled on the Slack app, the response carries a
        refresh_token + expires_in and the access token is short-lived; we keep
        both plus an absolute expiry so the worker can refresh before a send.
        Without rotation, the access token is long-lived and there's no refresh.
        """
        credentials = {"bot_token": data["access_token"]}
        if data.get("refresh_token"):
            credentials["refresh_token"] = data["refresh_token"]
            credentials["token_expires_at"] = int(time.time()) + int(data.get("expires_in", 43200))
        return credentials

    def exchange_code(
        self, code: str, redirect_uri: str, app: Mapping[str, str] | None = None
    ) -> dict:
        """Exchange the OAuth code for a per-workspace token. Routes inbound by
        (app, workspace), so provider_resource_id is api_app_id:team_id."""
        client_id, client_secret, _ = self._app(app)
        r = self._client.post(
            "/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise WebhookVerificationError(f"Slack OAuth failed: {data.get('error')}")
        team = data.get("team", {})
        app_id = data.get("app_id", "")
        return {
            "credentials": self._oauth_credentials(data),
            "provider_resource_id": f"{app_id}:{team.get('id', '')}",
            "address": f"slack:{team.get('name') or team.get('id')}",
        }

    def needs_refresh(self, credentials: Mapping[str, str] | None) -> bool:
        """True when a rotating access token is at/near expiry (120s buffer)."""
        creds = credentials or {}
        if not creds.get("refresh_token") or not creds.get("token_expires_at"):
            return False
        return time.time() >= int(creds["token_expires_at"]) - 120

    def refresh_credentials(self, credentials: Mapping[str, str]) -> dict:
        """Rotate the access token using the refresh token. Returns the full new
        credentials dict (Slack rotates the refresh token too, so both change)."""
        client_id, client_secret, _ = self._app(credentials)
        r = self._client.post(
            "/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
            },
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise WebhookVerificationError(f"Slack token refresh failed: {data.get('error')}")
        return self._oauth_credentials(data)

    # Messaging

    def _post(self, token: str, channel: str, text: str, thread_ts: str | None,
              username: str | None = None, icon_url: str | None = None,
              blocks: list | None = None):
        # `text` stays as the notification/accessibility fallback even when blocks
        # carry the visible content (Slack recommends always sending it).
        body: dict = {"channel": channel, "text": text}
        if blocks:
            body["blocks"] = blocks
        if thread_ts:
            body["thread_ts"] = thread_ts
        # Per-message identity override (needs the chat:write.customize scope) so a
        # shared Slack app posts under the developer's own name + icon.
        if username:
            body["username"] = username
        if icon_url:
            body["icon_url"] = icon_url
        r = self._client.post(
            "/chat.postMessage",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack chat.postMessage failed: {data.get('error')}")
        return data

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # The OAuth callback already set the address/resource id; provision is a
        # no-op confirmation that keeps the worker flow uniform.
        return ProvisionResult(
            address=(request.credentials or {}).get("address", "slack"),
            provider_resource_id=(request.credentials or {}).get("provider_resource_id", ""),
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        creds = credentials or {}
        channel = message.to[0]
        kit = blocks_render.to_slack(message.blocks) if message.blocks else None
        if message.media:
            kit = (kit or []) + _media_blocks(message.media)
        data = self._post(creds["bot_token"], channel, message.text or "", None,
                          username=creds.get("display_name"), icon_url=creds.get("icon_url"),
                          blocks=kit)
        return SendResult(
            provider_message_id=f"{channel}:{data['ts']}", provider_thread_id=channel
        )

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        creds = credentials or {}
        channel, ts = split_composite_id(provider_message_id)
        kit = blocks_render.to_slack(message.blocks) if message.blocks else None
        if message.media:
            kit = (kit or []) + _media_blocks(message.media)
        data = self._post(creds["bot_token"], channel, message.text or "", ts,
                          username=creds.get("display_name"), icon_url=creds.get("icon_url"),
                          blocks=kit)
        return SendResult(
            provider_message_id=f"{channel}:{data['ts']}", provider_thread_id=channel
        )

    def react(self, provider_message_id: str, emoji: str, credentials=None) -> None:
        """Add an emoji reaction to a message (reactions.add). Slack wants the
        short name without colons, so ':thumbsup:' / '👍' both normalize to it."""
        creds = credentials or {}
        channel, ts = split_composite_id(provider_message_id)
        name = emoji.strip(":")
        r = self._client.post(
            "/reactions.add",
            json={"channel": channel, "timestamp": ts, "name": name},
            headers={"Authorization": f"Bearer {creds['bot_token']}"},
        )
        r.raise_for_status()
        data = r.json()
        # already_reacted is a success for our purposes (the reaction is present).
        if not data.get("ok") and data.get("error") != "already_reacted":
            raise RuntimeError(f"Slack reactions.add failed: {data.get('error')}")

    def _verify_signature(
        self, payload: bytes, headers: Mapping[str, str], data: dict, credentials
    ) -> None:
        """Verify the Slack request signature. Uses the signing secret of the app
        that SENT this (identified by api_app_id in the pool), else the
        connection's stored secret."""
        api_app_id = data.get("api_app_id", "")
        signing_secret = self._app_by_id(api_app_id).get("signing_secret", "") if api_app_id else ""
        if not signing_secret:
            _, _, signing_secret = self._app(credentials)
        if not signing_secret:
            return
        h = lower_headers(headers)
        ts = h.get("x-slack-request-timestamp", "")
        sig = h.get("x-slack-signature", "")
        # Reject stale/unparseable timestamps before checking the signature, so a
        # captured signed request can't be replayed later.
        try:
            skew = abs(time.time() - int(ts))
        except ValueError:
            raise WebhookVerificationError("Slack timestamp missing or invalid") from None
        if skew > MAX_TIMESTAMP_SKEW:
            raise WebhookVerificationError("Slack timestamp too old")
        basestring = f"v0:{ts}:".encode() + payload
        expected = "v0=" + hmac.new(
            signing_secret.encode(), basestring, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise WebhookVerificationError("Slack signature mismatch")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        self._verify_signature(payload, headers, data, credentials)
        if data.get("type") == "url_verification":
            # handled in the route (returns the challenge); no messages here
            return []
        return parse_event(data)

    @staticmethod
    def interaction_route_key(payload: bytes) -> str | None:
        """Route an interactivity POST (form-encoded `payload=<json>`) by
        api_app_id:team_id, same key as the Events API."""
        data = _interaction_payload(payload)
        if data is None:
            return None
        app_id = data.get("api_app_id", "")
        team = (data.get("team") or {}).get("id", "") or data.get("team_id", "")
        if not (app_id or team):
            return None
        return f"{app_id}:{team}"

    def parse_interaction(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        """Verify and parse a Slack interactivity POST (a button tap). The body is
        form-encoded `payload=<json>`; the signature is over that raw body."""
        data = _interaction_payload(payload)
        if data is None:
            raise WebhookVerificationError("invalid Slack interaction payload")
        self._verify_signature(payload, headers, data, credentials)
        return parse_block_actions(data)
