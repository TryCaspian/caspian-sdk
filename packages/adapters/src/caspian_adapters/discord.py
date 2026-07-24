"""Discord adapter (bot token, one bot per connection).

Discord has no API to create bots, so each developer supplies their own bot
token (from discord.com/developers) at connect time - the same bring-your-own
pattern as Telegram. We store it per connection and route by application id.

Outbound is the Discord REST API (POST /channels/{id}/messages). Inbound for
normal channel messages arrives over Discord's persistent WebSocket Gateway
(NOT a webhook); the listeners.discord_gateway client holds that WebSocket and
feeds each MESSAGE_CREATE into the same event pipeline as webhooks, so Discord
is fully two-way. provider_message_id is "{channel_id}:{message_id}" so a reply
routes without extra lookups.
"""

import json
from collections.abc import Mapping

import httpx

from .base import (
    Attachment,
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    split_composite_id,
)

API = "https://discord.com/api/v10"


def parse_gateway_message(
    event: dict, application_id: str, route_by_guild: bool = False
) -> list[InboundMessage]:
    """Normalize a Discord MESSAGE_CREATE gateway event into our schema.

    Routing key (provider_inbox_id) selects which connection the message belongs
    to. For a BYO bot (one bot = one connection) that's the application id. For
    the shared "Caspian" bot (one bot in many servers), it's the guild id - each
    developer's server maps to their own connection. Shared-bot DMs have no guild
    to route by, so they're dropped (guild messages only)."""
    data = event.get("d") or event
    if event.get("t") not in (None, "MESSAGE_CREATE"):
        return []
    if data.get("author", {}).get("bot"):
        return []  # ignore other bots (and our own echoes) by default

    content = data.get("content")

    attachments = [
        Attachment(
            url=attachment.get("url"),
            filename=attachment.get("filename"),
            mime_type=attachment.get("content_type"),
            size_bytes=attachment.get("size"),
        )
        for attachment in data.get("attachments", [])
    ]

    # Allow attachment-only messages.
    if not content and not attachments:
        return []

    guild_id = data.get("guild_id")
    if route_by_guild:
        if guild_id is None:
            return []  # shared bot: no guild to route a DM by - skip
        inbox_id = str(guild_id)
    else:
        inbox_id = application_id

    channel_id = str(data["channel_id"])
    author = data.get("author", {})

    return [
        InboundMessage(
            external_event_id=str(data["id"]),
            provider_inbox_id=inbox_id,
            provider_message_id=f"{channel_id}:{data['id']}",
            provider_thread_id=channel_id,
            sender_address=author.get("username") or str(author.get("id", "")) or None,
            sender_name=author.get("global_name") or author.get("username"),
            text=content,
            chat_type="dm" if guild_id is None else "guild",
            attachments=tuple(attachments),
        )
    ]


def set_bot_nickname(base_url: str, bot_token: str, guild_id: str, nick: str) -> None:
    """Set the shared bot's OWN nickname in one server, so it shows the developer's
    custom name there (per-server branding on a shared bot). Needs CHANGE_NICKNAME
    in the invite. Best-effort: the caller ignores failures."""
    r = httpx.patch(
        f"{base_url}/guilds/{guild_id}/members/@me",
        json={"nick": nick[:32]},  # Discord nickname max length is 32
        headers={"Authorization": f"Bot {bot_token}"},
        timeout=15.0,
    )
    r.raise_for_status()


def install_url(base: str, client_id: str, permissions: str, redirect_uri: str,
                state: str) -> str:
    """The Discord 'add bot to server' OAuth URL for the shared Caspian bot."""
    from urllib.parse import urlencode

    origin = base.split("/api")[0]  # https://discord.com
    q = urlencode({
        "client_id": client_id,
        "scope": "bot",
        "permissions": permissions,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    })
    return f"{origin}/oauth2/authorize?{q}"


def webhook_id_from_url(url: str) -> str:
    """`.../webhooks/{id}/{token}` -> `{id}`."""
    parts = url.rstrip("/").split("/")
    if "webhooks" not in parts or parts.index("webhooks") + 1 >= len(parts):
        raise ValueError("not a valid Discord webhook URL")
    return parts[parts.index("webhooks") + 1]


class DiscordProvider:
    name = "discord"
    channel = "discord"
    # Identity is a bot_token OR a channel webhook_url (custom name/avatar per
    # message, no bot needed) - at least one, validated at provision.
    connect_credentials = ()
    optional_connect_credentials = ("bot_token", "webhook_url", "username", "avatar_url")
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
            Capability.GROUP_VISIBILITY,
            Capability.SEE_BOTS,
        }
    )

    def __init__(self, base_url: str = API, shared_bot_token: str = "") -> None:
        self._base = base_url
        self._shared_bot_token = shared_bot_token
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def _token(self, credentials: Mapping[str, str] | None) -> str:
        # Per-connection bot token (BYO), else the deployment's shared bot token
        # (a connection installed via the one-click OAuth flow has no token of its
        # own - it rides the shared Caspian bot).
        token = (credentials or {}).get("bot_token", "") or self._shared_bot_token
        if not token:
            raise ValueError("connection is missing a bot_token credential")
        return token

    def _post_message(self, token: str, channel_id: str, text: str, reply_to: str | None):
        body: dict = {"content": text}
        if reply_to:
            body["message_reference"] = {"message_id": reply_to}
        r = self._client.post(
            f"/channels/{channel_id}/messages",
            json=body,
            headers={"Authorization": f"Bot {token}"},
        )
        r.raise_for_status()
        return r.json()

    def typing(self, provider_thread_id: str, credentials=None) -> None:
        """Show the 'Caspian is typing…' indicator in the channel (~10s) while the
        agent thinks. Best-effort; a webhook-identity connection (no bot token)
        has no typing indicator, so this raises and the caller skips it."""
        token = self._token(credentials)
        r = self._client.post(
            f"/channels/{provider_thread_id}/typing",
            headers={"Authorization": f"Bot {token}"},
        )
        r.raise_for_status()

    def _post_webhook(self, credentials: Mapping[str, str], text: str):
        """Post through a channel webhook with the agent's custom name/avatar."""
        body: dict = {"content": text}
        if credentials.get("username"):
            body["username"] = credentials["username"]
        if credentials.get("avatar_url"):
            body["avatar_url"] = credentials["avatar_url"]
        r = httpx.post(f"{credentials['webhook_url']}?wait=true", json=body, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        return str(data.get("channel_id", "")), str(data.get("id", ""))

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        if creds.get("webhook_url"):
            r = self._client.get(creds["webhook_url"])
            r.raise_for_status()
            hook = r.json()
            name = creds.get("username") or hook.get("name") or "webhook"
            return ProvisionResult(
                address=name, provider_resource_id=webhook_id_from_url(creds["webhook_url"])
            )
        token = self._token(creds)
        r = self._client.get(
            "/applications/@me", headers={"Authorization": f"Bot {token}"}
        )
        r.raise_for_status()
        app = r.json()
        name = app.get("name") or app["id"]
        return ProvisionResult(address=f"#{name}", provider_resource_id=str(app["id"]))

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        if message.attachments:
            raise NotImplementedError(
                "DiscordProvider does not support outbound attachments."
            )

        creds = credentials or {}
        if creds.get("webhook_url"):
            channel_id, msg_id = self._post_webhook(creds, message.text or "")
            return SendResult(
                provider_message_id=f"{channel_id}:{msg_id}",
                provider_thread_id=channel_id,
            )

        token = self._token(credentials)
        channel_id = message.to[0]
        result = self._post_message(token, channel_id, message.text or "", None)
        return SendResult(
            provider_message_id=f"{channel_id}:{result['id']}",
            provider_thread_id=str(channel_id),
        )

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        # recipient is a channel id the bot can post to
        return self.send(provider_inbox_id, OutboundMessage(text=message.text, to=(recipient,)),
                         credentials=credentials)

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        if message.attachments:
            raise NotImplementedError(
                "DiscordProvider does not support outbound attachments."
            )

        creds = credentials or {}
        if creds.get("webhook_url"):
            # webhooks can't reply-reference; post the message with the identity
            channel_id, msg_id = self._post_webhook(creds, message.text or "")
            return SendResult(
                provider_message_id=f"{channel_id}:{msg_id}",
                provider_thread_id=channel_id,
            )

        token = self._token(credentials)
        channel_id, target = split_composite_id(provider_message_id)
        result = self._post_message(token, channel_id, message.text or "", target)
        return SendResult(
            provider_message_id=f"{channel_id}:{result['id']}",
            provider_thread_id=channel_id,
        )

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        # Normal Discord messages arrive over the Gateway listener, which bridges
        # each MESSAGE_CREATE to this connection's scoped webhook. The scoped route
        # supplies the connection's resource id (the application id) in credentials.
        if credentials is None:
            raise WebhookVerificationError("discord webhooks require a connection scope")
        try:
            event = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON payload") from exc
        app_id = (credentials or {}).get("provider_resource_id", "")
        return parse_gateway_message(event, app_id)
