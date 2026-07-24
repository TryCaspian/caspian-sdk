"""In-memory Discord / Slack / Instagram / Facebook providers for tests.

Each consumes the real inbound shape of its platform so the gateway wiring is
exercised on the same normalization path as the live adapters.
"""

import json
import secrets
from collections.abc import Mapping
from urllib.parse import urlencode

from .base import (
    InboundEvent,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
    lower_headers,
)
from .discord import DiscordProvider, parse_gateway_message
from .messenger import InstagramProvider, parse_messaging_webhook
from .slack import (
    SlackProvider,
    decode_form,
    is_urlencoded,
    parse_event,
    parse_slash_command,
)


class FakeDiscordProvider:
    name = "fake-discord"
    channel = "discord"
    capabilities = DiscordProvider.capabilities
    connect_credentials = ()
    optional_connect_credentials = ("bot_token", "webhook_url", "username", "avatar_url")

    def __init__(self) -> None:
        self.app_id = str(9_100_000 + secrets.randbelow(100_000))
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def _app(self, credentials):
        token = (credentials or {}).get("bot_token")
        if not token:
            return self.app_id
        import base64

        first = token.split(".", 1)[0]
        try:
            return base64.b64decode(first + "=" * (-len(first) % 4)).decode()
        except Exception:
            return first

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        if creds.get("webhook_url"):
            from .discord import webhook_id_from_url

            name = creds.get("username") or "webhook"
            return ProvisionResult(address=name,
                                   provider_resource_id=webhook_id_from_url(creds["webhook_url"]))
        return ProvisionResult(address="#fake-bot",
                               provider_resource_id=self._app(creds))

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        creds = credentials or {}
        if creds.get("webhook_url"):
            self.sent.append({"webhook": creds["webhook_url"], "username": creds.get("username"),
                              "text": message.text})
            return SendResult(provider_message_id=f"wh:{secrets.randbelow(99999)}",
                              provider_thread_id="wh")
        cid = message.to[0]
        self.sent.append({"channel": cid, "text": message.text})
        return SendResult(provider_message_id=f"{cid}:{secrets.randbelow(99999)}",
                          provider_thread_id=str(cid))

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        cid, _, target = provider_message_id.partition(":")
        self.replies.append({"channel": cid, "in_reply_to": target, "text": message.text})
        return SendResult(provider_message_id=f"{cid}:{secrets.randbelow(99999)}",
                          provider_thread_id=cid)

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            event = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON") from exc
        app_id = (credentials or {}).get("provider_resource_id", self.app_id)
        return parse_gateway_message(event, app_id)

    def webhook_payload(self, *, channel_id="chan1", text="Hi there", author="customer"):
        self._seq += 1
        return {
            "t": "MESSAGE_CREATE",
            "d": {
                "id": str(700000 + self._seq),
                "channel_id": channel_id,
                "content": text,
                "author": {"id": str(555000 + self._seq), "username": author},
            },
        }


class FakeSlackProvider:
    name = "fake-slack"
    channel = "slack"
    capabilities = SlackProvider.capabilities
    connect_credentials = ()
    oauth = True
    client_id = "fake-shared-client"  # non-empty = a shared app is "configured"

    def __init__(self, rotating: bool = False, token_ttl: int = 43200) -> None:
        self.team_id = f"T{secrets.token_hex(4).upper()}"
        self.app_id = f"A{secrets.token_hex(4).upper()}"
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self.reactions: list[dict] = []
        self.refreshes = 0
        self._seq = 0
        self._rotating = rotating
        self._ttl = token_ttl

    @staticmethod
    def route_key(payload: bytes) -> str | None:
        # Composite api_app_id:team_id, matching the real provider (the pool lets
        # several apps live in one workspace, so team alone isn't unique), including
        # its urlencoded fallback for slash commands.
        return SlackProvider.route_key(payload)

    def pool_size(self) -> int:
        return 1

    def app_at(self, index: int) -> dict:
        return {"app_id": self.app_id, "client_id": self.client_id,
                "client_secret": "fake-secret", "signing_secret": "fake-signing"}

    def authorize_url(self, redirect_uri: str, state: str, app=None) -> str:
        return f"https://slack.com/oauth/v2/authorize?state={state}&redirect_uri={redirect_uri}"

    def _creds(self, tag: str) -> dict:
        import time as _t

        creds = {"bot_token": f"xoxb-fake-{tag}"}
        if self._rotating:
            creds["refresh_token"] = f"xoxe-fake-{tag}-{secrets.token_hex(3)}"
            creds["token_expires_at"] = int(_t.time()) + self._ttl
        return creds

    def exchange_code(self, code: str, redirect_uri: str, app=None) -> dict:
        return {
            "credentials": self._creds(code),
            # api_app_id:team_id, like the real provider (routes by app+workspace)
            "provider_resource_id": f"{self.app_id}:{self.team_id}",
            "address": f"slack:{self.team_id}",
        }

    def needs_refresh(self, credentials) -> bool:
        import time as _t

        creds = credentials or {}
        if not creds.get("refresh_token") or not creds.get("token_expires_at"):
            return False
        return _t.time() >= int(creds["token_expires_at"]) - 120

    def refresh_credentials(self, credentials) -> dict:
        self.refreshes += 1
        return self._creds(f"rotated{self.refreshes}")

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(
            address=(request.credentials or {}).get("address", "slack"),
            provider_resource_id=(request.credentials or {}).get(
                "provider_resource_id", f"{self.app_id}:{self.team_id}"),
        )

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        ch = message.to[0]
        self.sent.append({"channel": ch, "text": message.text})
        ts = f"{self._next()}.0001"
        return SendResult(provider_message_id=f"{ch}:{ts}", provider_thread_id=ch)

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        ch, _, ts = provider_message_id.partition(":")
        self.replies.append({"channel": ch, "thread_ts": ts, "text": message.text})
        return SendResult(provider_message_id=f"{ch}:{self._next()}.0002", provider_thread_id=ch)

    def _next(self):
        self._seq += 1
        return 1_752_000_000 + self._seq

    def react(self, provider_inbox_id, provider_message_id, emoji, credentials=None) -> None:
        channel, _, ts = provider_message_id.partition(":")
        self.reactions.append({"channel": channel, "ts": ts, "emoji": emoji.strip(":")})

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundEvent]:
        # Slash commands arrive urlencoded rather than as JSON; branch the same way
        # the real provider does so tests exercise the live decode path.
        if is_urlencoded(lower_headers(headers or {}).get("content-type", "")):
            return parse_slash_command(decode_form(payload))
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON") from exc
        if data.get("type") == "url_verification":
            return []
        return parse_event(data)

    def _envelope(self, event: dict) -> dict:
        self._seq += 1
        return {
            "team_id": self.team_id,
            "api_app_id": self.app_id,
            "event_id": f"Ev{self._seq}",
            "event": event,
        }

    def webhook_payload(self, *, channel="C123", text="Hi there", user="U456"):
        return self._envelope(
            {
                "type": "message",
                "channel": channel,
                "user": user,
                "text": text,
                "ts": f"{self._next()}.0000",
                "channel_type": "channel",
            }
        )

    def reaction_payload(
        self, *, channel="C123", ts="1752000000.0000", user="U456",
        reaction="thumbsup", action="added", item_type="message",
    ):
        """A reaction_added/reaction_removed callback in Slack's real envelope shape."""
        return self._envelope(
            {
                "type": f"reaction_{action}",
                "user": user,
                "reaction": reaction,
                "item": {"type": item_type, "channel": channel, "ts": ts},
                "event_ts": f"{self._next()}.0000",
            }
        )

    def command_body(
        self, *, command="/status", text="", channel="C123", channel_name="general",
        user="U456", user_name="steve",
    ) -> bytes:
        """A slash-command post body: urlencoded bytes, not JSON, like the real thing."""
        self._seq += 1
        return urlencode(
            {
                "token": "fake-verification-token",
                "team_id": self.team_id,
                "api_app_id": self.app_id,
                "channel_id": channel,
                "channel_name": channel_name,
                "user_id": user,
                "user_name": user_name,
                "command": command,
                "text": text,
                "response_url": f"https://hooks.slack.test/commands/{self.team_id}/{self._seq}",
                "trigger_id": f"{self._seq}.{self.team_id}.fake",
            }
        ).encode()


class _FakeMetaMessaging:
    channel = "override"
    name = "override"
    capabilities = InstagramProvider.capabilities

    def __init__(self) -> None:
        self.page_id = str(2_000_000 + secrets.randbelow(100_000))
        self.sent: list[dict] = []
        self.replies: list[dict] = []
        self._seq = 0

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(address=f"{self.channel}:{self.page_id}",
                               provider_resource_id=self.page_id)

    def send(self, provider_inbox_id, message: OutboundMessage, credentials=None) -> SendResult:
        to = message.to[0]
        self.sent.append({"to": to, "text": message.text})
        return SendResult(provider_message_id=f"{to}:m{self._seq}", provider_thread_id=to)

    def reply(
        self, provider_inbox_id, provider_message_id, message, credentials=None
    ) -> SendResult:
        to, _, _ = provider_message_id.partition(":")
        self.replies.append({"to": to, "text": message.text})
        self._seq += 1
        return SendResult(provider_message_id=f"{to}:m{self._seq}", provider_thread_id=to)

    def meta_verify(self, params: Mapping[str, str]) -> str | None:
        return params.get("hub.challenge") if params.get("hub.mode") == "subscribe" else None

    def parse_webhook(self, payload, headers, credentials=None) -> list[InboundMessage]:
        try:
            json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("invalid JSON") from exc
        return parse_messaging_webhook(payload, self.page_id, self.channel)

    def webhook_payload(self, *, sender="9998887776", text="Hi there"):
        self._seq += 1
        return {
            "object": self.channel,
            "entry": [
                {
                    "id": self.page_id,
                    "messaging": [
                        {
                            "sender": {"id": sender},
                            "recipient": {"id": self.page_id},
                            "message": {"mid": f"mid{self._seq}", "text": text},
                        }
                    ],
                }
            ],
        }


class FakeInstagramProvider(_FakeMetaMessaging):
    name = "fake-instagram"
    channel = "instagram"


class FakeFacebookProvider(_FakeMetaMessaging):
    name = "fake-facebook"
    channel = "facebook"
