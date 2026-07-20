"""Google Meet adapter (channel "gmeet").

The control-plane half of a Meet-native agent: it creates a Meet space (the
reusable meeting container), hands back the join link, and normalizes inbound
Meet events. It is the exact same split as the voice provider — the gateway
orchestrates, the realtime audio/video loop lives OUTSIDE it. Here the "realtime
layer" is the persona bot that actually joins the meeting with a virtual
mic/camera; this provider dispatches it at a configured `join_url` (mirroring
voice's ConversationRelay) and never touches media itself.

Transport: the Google Meet REST API (GA). Meet spaces are user-owned, so the
gateway acts as a Workspace user via a service account with domain-wide
delegation: we mint an OAuth2 access token by signing a JWT with the service
account's private key (RS256, via `cryptography` — no extra deps) and exchanging
it at Google's token endpoint, impersonating `impersonate` (the `sub` claim).

  - Create a meeting: POST {meet}/v2/spaces (empty body) -> {name:"spaces/<id>",
    meetingUri:"https://meet.google.com/<code>", meetingCode:"abc-defg-hij"}.
  - Inbound: subscribe via the Google Workspace Events API (Pub/Sub or HTTPS
    push); each event is normalized by parse_webhook. An optional shared secret
    header is verified when configured (opt-in, like the other webhook providers).

provider_message_id is "{meetingCode}:{space_name}" so a reply re-dispatches the
persona to the same space without a lookup (meetingCode carries no ':').
"""

import base64
import hmac
import json
import time
from collections.abc import Mapping

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
)

# Minimum to create/read spaces; artifact (transcript/recording) reads add the
# readonly meetings scopes at the deployment's discretion.
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
)
TOKEN_URI = "https://oauth2.googleapis.com/token"
MEET_BASE_URL = "https://meet.googleapis.com"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
SECRET_HEADER = "x-caspian-meet-secret"

# Google Workspace Events "type" values we surface as inbound events. Kept as a
# mapping to the placeholder body recorded on the conversation; the meeting's
# turn-by-turn content is the realtime layer's concern, not this provider's.
_EVENT_TEXT = {
    "google.workspace.meet.participant.v2.joined": "[participant joined]",
    "google.workspace.meet.participant.v2.left": "[participant left]",
    "google.workspace.meet.conference.v2.started": "[meeting started]",
    "google.workspace.meet.conference.v2.ended": "[meeting ended]",
    "google.workspace.meet.transcript.v2.fileGenerated": "[transcript ready]",
    "google.workspace.meet.recording.v2.fileGenerated": "[recording ready]",
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_sa_jwt(
    sa_info: Mapping[str, str], subject: str, scopes, token_uri: str, now: float
) -> str:
    """Sign the service-account assertion JWT (RS256) for the token exchange.

    `sa_info` is the parsed service-account key JSON; the private key rides in
    its `private_key` PEM field. `subject` is the Workspace user to impersonate.
    """
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": sa_info["client_email"],
        "sub": subject,
        "scope": " ".join(scopes),
        "aud": token_uri,
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    key = serialization.load_pem_private_key(sa_info["private_key"].encode(), password=None)
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


class GoogleMeetProvider:
    name = "google-meet"
    channel = "gmeet"
    connect_credentials: tuple[str, ...] = ()
    # A Meet identity creates meetings (INITIATE) and receives meeting events
    # (RECEIVE via Workspace Events). It carries no text SEND/REPLY semantics —
    # in-meeting turns are the realtime layer's job — so those are absent, exactly
    # like the voice provider.
    capabilities = frozenset({Capability.INITIATE, Capability.RECEIVE})

    def __init__(
        self,
        sa_info: Mapping[str, str],
        impersonate: str,
        join_url: str = "",
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
        meet_base_url: str = MEET_BASE_URL,
        token_uri: str = TOKEN_URI,
        webhook_secret: str = "",
        http: httpx.Client | None = None,
        clock=time.time,
    ) -> None:
        if not (sa_info and sa_info.get("client_email") and sa_info.get("private_key")):
            raise ValueError(
                "COMM_GMEET_SA_JSON must be a service-account key with client_email "
                "and private_key for the google-meet provider"
            )
        if not impersonate:
            raise ValueError(
                "COMM_GMEET_IMPERSONATE (the Workspace user to act as) is required "
                "for the google-meet provider"
            )
        self._sa = dict(sa_info)
        self._impersonate = impersonate
        self._join_url = join_url
        self._scopes = tuple(scopes)
        self._meet = meet_base_url.rstrip("/")
        self._token_uri = sa_info.get("token_uri", token_uri)
        self._webhook_secret = webhook_secret
        self._clock = clock
        self._client = http or httpx.Client(timeout=30.0)
        self._access_token = ""
        self._token_exp = 0.0

    # --- auth -----------------------------------------------------------------

    def _token(self) -> str:
        """A cached OAuth2 access token, minted via the SA JWT-bearer flow."""
        now = self._clock()
        if self._access_token and now < self._token_exp - 60:
            return self._access_token
        assertion = sign_sa_jwt(self._sa, self._impersonate, self._scopes, self._token_uri, now)
        resp = self._client.post(
            self._token_uri,
            data={"grant_type": JWT_BEARER, "assertion": assertion},
        )
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        self._token_exp = now + float(body.get("expires_in", 3600))
        return self._access_token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    # --- meeting lifecycle ----------------------------------------------------

    def _create_space(self) -> dict:
        """Create a Meet space; returns {name, meetingUri, meetingCode}."""
        resp = self._client.post(f"{self._meet}/v2/spaces", headers=self._auth_headers(), json={})
        resp.raise_for_status()
        return resp.json()

    def _dispatch_persona(self, space: dict, persona: str | None, recipient: str | None) -> None:
        """Tell the external realtime bot to join `meetingUri` as the persona.

        Best-effort and side-channel, mirroring how voice bridges a call to its
        ConversationRelay endpoint. No-op when no join_url is configured (the
        caller can still hand the returned link to a human to join manually).
        """
        if not self._join_url:
            return
        self._client.post(
            self._join_url,
            json={
                "meetingUri": space.get("meetingUri"),
                "meetingCode": space.get("meetingCode"),
                "space": space.get("name"),
                "persona": persona or self._impersonate,
                "recipient": recipient,
            },
        )

    def _result(self, space: dict) -> SendResult:
        return SendResult(
            provider_message_id=f"{space.get('meetingCode', '')}:{space.get('name', '')}",
            provider_thread_id=space.get("meetingCode", ""),
        )

    # --- provider contract ----------------------------------------------------

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # The agent's Meet identity is the impersonated Workspace user; spaces are
        # minted per meeting on send/initiate, not at connect time. Connectivity
        # to Google is intentionally not checked here so connect never blocks.
        return ProvisionResult(address=self._impersonate, provider_resource_id=self._impersonate)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        # A "message" on the meet channel is a meeting: create a space and send
        # the persona in to meet the recipient. The join link is meetingUri.
        space = self._create_space()
        self._dispatch_persona(space, None, message.to[0] if message.to else None)
        return self._result(space)

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        # No in-meeting text reply; start a fresh meeting for the same thread.
        space = self._create_space()
        self._dispatch_persona(space, None, None)
        return self._result(space)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        space = self._create_space()
        self._dispatch_persona(space, None, recipient)
        return self._result(space)

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        """Normalize a Google Workspace Events push (a Meet event) into our schema.

        Subscribe to Meet events via the Workspace Events API (Pub/Sub or HTTPS
        push). Each delivery carries the CloudEvent `type` (the Meet event) and a
        `data` object referencing the conferenceRecord / participant. We record an
        event so the gateway has a conversation to anchor to; the meeting's actual
        content flows through the realtime layer, not here.
        """
        if self._webhook_secret:
            received = {k.lower(): v for k, v in headers.items()}.get(SECRET_HEADER, "")
            if not hmac.compare_digest(received, self._webhook_secret):
                raise WebhookVerificationError("Meet webhook secret mismatch")
        try:
            event = json.loads(payload)
        except (ValueError, TypeError):
            return []
        etype = event.get("type") or event.get("ce-type") or ""
        if etype not in _EVENT_TEXT:
            return []
        data = event.get("data") or {}
        # The conferenceRecord ("conferenceRecords/<id>") is the meeting instance;
        # fall back to the space if that's all the push carries.
        conference = data.get("conferenceRecord") or data.get("space") or etype
        participant = data.get("participant") or {}
        signed_in = participant.get("signedinUser") or {}
        sender_name = signed_in.get("displayName") or participant.get("name")
        event_id = event.get("id") or f"{conference}:{etype}"
        return [
            InboundMessage(
                external_event_id=event_id,
                provider_inbox_id=self._impersonate,
                provider_message_id=f"{conference}:{event_id}",
                provider_thread_id=conference,
                sender_address=signed_in.get("user") or None,
                sender_name=sender_name,
                recipients=[{"address": self._impersonate}],
                text=_EVENT_TEXT[etype],
                chat_type="gmeet",
            )
        ]
