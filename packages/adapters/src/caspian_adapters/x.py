"""X (Twitter) adapter: post tweets + reactive DMs on the X API v2.

Two surfaces, one hard boundary:
- Post a tweet (broadcast/discovery) -- POST /2/tweets.
- Reactive DM: a human DMs the agent first, the agent DMs back. Inbound DMs
  arrive via the Account Activity API webhook; replies go out on
  POST /2/dm_conversations/with/{participant_id}/messages.

REACTIVE ONLY. This provider never cold-starts a DM (no Capability.INITIATE)
and does no bulk/unsolicited outreach -- those are X-ToS-banned. The connected
account is assumed to already exist, be labelled "Automated", and be connected
via OAuth (the agent supplies its user access token + numeric user id).

Multi-tenant: each connection carries its own OAuth
user access token + user id in stored credentials; one X app fans every
subscribed user's inbound into ONE webhook URL, routed by the payload's
`for_user_id` (== the connection's provider_resource_id). Inbound is verified
with the `x-twitter-webhooks-signature` header (base64 HMAC-SHA256 of the raw
body with the app's consumer secret -- one app, so the secret is
deployment-level). X's periodic CRC GET challenge is answered by
verify_challenge() from the webhook route.

Send convention: `message.to[0]` prefixed `dm:<user_id>` routes to a DM;
anything else (including empty) posts a tweet. provider_message_id is the tweet
id for a tweet, or `dm:<user_id>:<dm_event_id>` for a DM (so reply() can parse
the participant back out).

Current X API v2 endpoints verified against docs.x.com (2026):
- POST https://api.x.com/2/tweets                              (create post)
- POST https://api.x.com/2/dm_conversations/with/{id}/messages (send a DM)
- Account Activity API webhook: GET CRC challenge (crc_token) + POST DM events,
  x-twitter-webhooks-signature = "sha256=" + base64(HMAC-SHA256(secret, body)).
Auth: OAuth 1.0a user context (app consumer key/secret + the account's access
token/secret, HMAC-SHA1 signed) when those are configured -- non-expiring, ideal
for a single connected account. Falls back to an OAuth 2.0 user-context bearer
token (scopes tweet.read, tweet.write, users.read, dm.read, dm.write,
offline.access) when only a bearer-style access token is supplied.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
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
)

log = logging.getLogger("comm.x")


def _pct(value: str) -> str:
    """RFC 3986 percent-encoding as OAuth 1.0a requires (space -> %20, ~ kept)."""
    return urllib.parse.quote(str(value), safe="~")


def oauth1_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    params: Mapping[str, str] | None = None,
) -> str:
    """Build an OAuth 1.0a HMAC-SHA1 Authorization header for a request.

    X API v2 signs the request line + oauth params (+ any query params); JSON
    request bodies are NOT part of the signature base string, so `params` covers
    only query-string params when present.
    """
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    allp = {**(params or {}), **oauth}
    base_str = "&".join(f"{_pct(k)}={_pct(allp[k])}" for k in sorted(allp))
    base = f"{method.upper()}&{_pct(url)}&{_pct(base_str)}"
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def parse_x_webhook(payload: bytes, for_user_fallback: str = "") -> list[InboundMessage]:
    """Turn an Account Activity DM-event payload into InboundMessages.

    Skips the agent's own outbound echoes (`sender_id == for_user_id`) so the
    agent never re-processes messages it just sent.
    """
    data = json.loads(payload)
    for_user_id = data.get("for_user_id") or for_user_fallback
    users = data.get("users", {}) or {}
    out: list[InboundMessage] = []
    for event in data.get("direct_message_events", []):
        if event.get("type") != "message_create":
            continue
        message_create = event.get("message_create", {})
        sender_id = message_create.get("sender_id")
        if not sender_id or sender_id == for_user_id:
            # our own outbound echo (or malformed) -- never re-ingest it
            continue
        event_id = event.get("id")
        text = message_create.get("message_data", {}).get("text")
        sender = users.get(sender_id, {})
        out.append(
            InboundMessage(
                external_event_id=event_id,
                provider_inbox_id=for_user_id,
                provider_message_id=f"dm:{sender_id}:{event_id}",
                provider_thread_id=f"dm:{sender_id}",
                sender_address=sender_id,
                sender_name=sender.get("name") or sender.get("screen_name"),
                recipients=[{"address": for_user_id}],
                text=text,
                chat_type="x_dm",
            )
        )
    return out


def _oauth1_auth(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    extra: Mapping[str, str],
    token_secret: str = "",
) -> str:
    """Sign a 2-/3-legged OAuth 1.0a request (no user token, or a temp request
    token). `extra` carries the leg-specific oauth params (oauth_callback for the
    request-token leg; oauth_token + oauth_verifier for the access-token leg)."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
        **dict(extra),
    }
    base_str = "&".join(f"{_pct(k)}={_pct(oauth[k])}" for k in sorted(oauth))
    base = f"{method.upper()}&{_pct(url)}&{_pct(base_str)}"
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def parse_dm_events(data: dict, for_user_id: str) -> list[InboundMessage]:
    """Turn a GET /2/dm_events response into InboundMessages (polling path).

    Same normalized shape as the Account Activity webhook (parse_x_webhook), but
    the v2 dm_events payload is flat: each event has id/sender_id/text/event_type,
    and `expansions=sender_id` puts sender profiles in includes.users. Skips the
    account's own outbound (`sender_id == for_user_id`) so we never re-ingest it.
    """
    users = {u["id"]: u for u in (data.get("includes", {}).get("users", []) or [])}
    out: list[InboundMessage] = []
    for ev in data.get("data", []) or []:
        if ev.get("event_type") != "MessageCreate":
            continue
        sender_id = ev.get("sender_id")
        if not sender_id or sender_id == for_user_id:
            continue
        event_id = ev.get("id")
        sender = users.get(sender_id, {})
        out.append(
            InboundMessage(
                external_event_id=event_id,
                provider_inbox_id=for_user_id,
                provider_message_id=f"dm:{sender_id}:{event_id}",
                provider_thread_id=f"dm:{sender_id}",
                sender_address=sender_id,
                sender_name=sender.get("name") or sender.get("username"),
                recipients=[{"address": for_user_id}],
                text=ev.get("text"),
                chat_type="x_dm",
            )
        )
    return out


class XProvider:
    name = "x"
    channel = "x"
    # SEND covers both posting a tweet and a reactive DM reply. Deliberately no
    # INITIATE: cold/bulk DM outreach is out of scope (X ToS).
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    # NOTE: no `oauth = True` marker. That flag routes ALL connects through the
    # generic OAuth flow; X instead offers BOTH bring-your-own tokens (connect_x)
    # AND one-click install (install_x -> dedicated /oauth/x/callback), so the
    # generic path must stay off.
    # The agent brings its own OAuth user access token + numeric user id.
    connect_credentials: tuple[str, ...] = ("access_token", "user_id")
    # access_secret is the OAuth 1.0a token secret for a bring-your-own account
    # (omit it and the connection signs with the deployment secret instead).
    optional_connect_credentials: tuple[str, ...] = ("username", "access_secret")

    def __init__(
        self,
        consumer_key: str = "",
        consumer_secret: str = "",
        access_token: str = "",
        access_secret: str = "",
        user_id: str = "",
        webhook_secret: str = "",
        base_url: str = "https://api.x.com",
    ) -> None:
        # App-level OAuth 1.0a consumer credentials (used to sign requests).
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        # Deployment fallback (a single account); per-connection creds win.
        self._default_access_token = access_token
        self._default_access_secret = access_secret
        self._default_user_id = user_id
        # Verifies inbound webhooks + signs the CRC challenge; falls back to the
        # app consumer secret when a distinct webhook secret isn't set.
        self._webhook_secret = webhook_secret or consumer_secret
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=30.0)

    def _token(self, credentials: Mapping[str, str] | None) -> str:
        creds = credentials or {}
        access_token = creds.get("access_token") or self._default_access_token
        if not access_token:
            raise ValueError(
                "x needs an OAuth user access token "
                "(per-connection credentials or COMM_X_ACCESS_TOKEN fallback)"
            )
        return access_token

    def _uses_oauth1(self, credentials: Mapping[str, str] | None) -> bool:
        """OAuth 1.0a when we have the app consumer pair + a user token secret;
        otherwise fall back to an OAuth 2.0 user bearer token."""
        creds = credentials or {}
        token_secret = creds.get("access_secret") or self._default_access_secret
        return bool(self._consumer_key and self._consumer_secret and token_secret)

    def _auth(
        self, credentials: Mapping[str, str] | None, method: str, path: str
    ) -> dict[str, str]:
        creds = credentials or {}
        if self._uses_oauth1(credentials):
            token = creds.get("access_token") or self._default_access_token
            token_secret = creds.get("access_secret") or self._default_access_secret
            header = oauth1_header(
                method,
                f"{self._base_url}{path}",
                self._consumer_key,
                self._consumer_secret,
                token,
                token_secret,
            )
            return {"Authorization": header}
        return {"Authorization": f"Bearer {self._token(credentials)}"}

    def _post_tweet(
        self, credentials: Mapping[str, str] | None, text: str, reply_to: str | None = None
    ) -> SendResult:
        body: dict = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        r = self._client.post(
            "/2/tweets", headers=self._auth(credentials, "POST", "/2/tweets"), json=body
        )
        r.raise_for_status()
        tweet_id = r.json()["data"]["id"]
        return SendResult(provider_message_id=tweet_id, provider_thread_id=tweet_id)

    def _send_dm(
        self, credentials: Mapping[str, str] | None, user_id: str, text: str
    ) -> SendResult:
        path = f"/2/dm_conversations/with/{user_id}/messages"
        r = self._client.post(
            path,
            headers=self._auth(credentials, "POST", path),
            json={"text": text},
        )
        r.raise_for_status()
        data = r.json()["data"]
        event_id = data.get("dm_event_id") or data.get("id")
        return SendResult(
            provider_message_id=f"dm:{user_id}:{event_id}",
            provider_thread_id=f"dm:{user_id}",
        )

    # One-click install (OAuth 1.0a 3-legged / "Sign in with X")

    def oauth_request_token(self, callback_url: str) -> dict:
        """Leg 1: get a temporary request token + the authorize URL to send the
        developer to. Signed with the shared Caspian app's consumer key/secret."""
        url = f"{self._base_url}/oauth/request_token"
        header = _oauth1_auth(
            "POST",
            url,
            self._consumer_key,
            self._consumer_secret,
            {"oauth_callback": callback_url},
        )
        r = self._client.post("/oauth/request_token", headers={"Authorization": header})
        r.raise_for_status()
        form = dict(urllib.parse.parse_qsl(r.text))
        if form.get("oauth_callback_confirmed") != "true":
            raise ValueError(f"X request_token failed: {r.text[:200]}")
        token = form["oauth_token"]
        return {
            "oauth_token": token,
            "oauth_token_secret": form["oauth_token_secret"],
            "authorize_url": f"https://api.x.com/oauth/authorize?oauth_token={token}",
        }

    def oauth_access_token(
        self, oauth_token: str, oauth_verifier: str, request_token_secret: str
    ) -> dict:
        """Leg 3: exchange the authorized request token for the account's
        NON-EXPIRING access token/secret + its numeric id and @handle."""
        url = f"{self._base_url}/oauth/access_token"
        header = _oauth1_auth(
            "POST",
            url,
            self._consumer_key,
            self._consumer_secret,
            {"oauth_token": oauth_token, "oauth_verifier": oauth_verifier},
            token_secret=request_token_secret,
        )
        r = self._client.post("/oauth/access_token", headers={"Authorization": header})
        r.raise_for_status()
        form = dict(urllib.parse.parse_qsl(r.text))
        return {
            "access_token": form["oauth_token"],
            "access_secret": form["oauth_token_secret"],
            "user_id": form.get("user_id", ""),
            "username": form.get("screen_name", ""),
        }

    def poll_dms(
        self, credentials: Mapping[str, str] | None, cursor: str | None = None
    ) -> tuple[list[InboundMessage], str]:
        """Poll GET /2/dm_events for new inbound DMs (the no-webhook inbound path).

        Returns (new_messages, new_cursor). `cursor` is the newest dm_event id
        seen last time; only events strictly newer are returned, oldest-first, so
        the agent processes a conversation in order. On the FIRST poll (cursor is
        None) we adopt the newest id as a baseline and return nothing - a bot must
        not reply to the whole DM history it inherits, only to messages that
        arrive after it comes online.

        Avoids the Account Activity API entirely (no per-app subscription cap, no
        enterprise gating): each connection polls with its own token.
        """
        creds = credentials or {}
        user_id = creds.get("user_id") or self._default_user_id
        params = {
            "dm_event.fields": "sender_id,created_at,event_type,text",
            "expansions": "sender_id",
            "user.fields": "name,username",
            "max_results": "50",
        }
        headers = self._auth(credentials, "GET", "/2/dm_events")
        # OAuth 1.0a signs query params too, so sign with them included.
        if headers["Authorization"].startswith("OAuth "):
            token = creds.get("access_token") or self._default_access_token
            token_secret = creds.get("access_secret") or self._default_access_secret
            headers = {
                "Authorization": oauth1_header(
                    "GET",
                    f"{self._base_url}/2/dm_events",
                    self._consumer_key,
                    self._consumer_secret,
                    token,
                    token_secret,
                    params=params,
                )
            }
        r = self._client.get("/2/dm_events", params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
        newest = cursor
        for ev in data.get("data", []) or []:
            eid = ev.get("id")
            if eid and (newest is None or int(eid) > int(newest)):
                newest = eid
        if cursor is None:
            return [], newest or "0"
        fresh = [
            m for m in parse_dm_events(data, user_id) if int(m.external_event_id) > int(cursor)
        ]
        fresh.sort(key=lambda m: int(m.external_event_id))
        return fresh, newest or cursor

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        user_id = creds.get("user_id") or self._default_user_id
        if not user_id:
            raise ValueError("x needs the connected account's numeric user_id")
        # address is the display handle (@screen_name) when known; resource_id is
        # the numeric user id that the AAA webhook routes by (for_user_id).
        handle = creds.get("username") or user_id
        return ProvisionResult(address=handle, provider_resource_id=user_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        target = message.to[0] if message.to else ""
        if target.startswith("dm:"):
            return self._send_dm(credentials, target[len("dm:") :], message.text or "")
        return self._post_tweet(credentials, message.text or "")

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        if provider_message_id.startswith("dm:"):
            # dm:<user_id>:<event_id> -- reply into the same DM conversation.
            _, user_id, _ = provider_message_id.split(":", 2)
            return self._send_dm(credentials, user_id, message.text or "")
        # Reply into a tweet thread.
        return self._post_tweet(credentials, message.text or "", reply_to=provider_message_id)

    def verify_challenge(self, params: Mapping[str, str]) -> dict | None:
        """Answer X's Account Activity CRC GET challenge.

        X sends a `crc_token`; we return
        {"response_token": "sha256=" + base64(HMAC-SHA256(consumer_secret, token))}
        within three seconds. None when there is no crc_token to answer.
        """
        crc_token = params.get("crc_token")
        if not crc_token:
            return None
        digest = hmac.new(
            self._webhook_secret.encode(), crc_token.encode(), hashlib.sha256
        ).digest()
        return {"response_token": "sha256=" + base64.b64encode(digest).decode()}

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        if self._webhook_secret:
            received = {k.lower(): v for k, v in headers.items()}.get(
                "x-twitter-webhooks-signature", ""
            )
            expected = (
                "sha256="
                + base64.b64encode(
                    hmac.new(self._webhook_secret.encode(), payload, hashlib.sha256).digest()
                ).decode()
            )
            if not hmac.compare_digest(received, expected):
                raise WebhookVerificationError("X webhook signature mismatch")
        # One X app fans every subscribed account's inbound into this one route;
        # the real account id rides in the payload's for_user_id. The credentials
        # arg is only a fallback, so never require _token() here.
        fallback = (credentials or {}).get("provider_resource_id") or self._default_user_id
        return parse_x_webhook(payload, fallback or "")
