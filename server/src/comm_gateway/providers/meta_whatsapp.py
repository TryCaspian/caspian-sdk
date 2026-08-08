"""WhatsApp adapter on the Meta Cloud API (direct, no BSP markup).

Multi-tenant: each connection carries its own `phone_number_id` + `access_token`
in its stored credentials (obtained via Embedded Signup — see meta_onboarding.py
and routes/onboarding.py). A deployment may also configure a single fallback
number (COMM_META_WA_*) for its own test line; a connection with no per-connection
credentials uses that.

Send: POST /{phone_number_id}/messages with the connection's bearer token.
Provision: subscribe our app to the connection's WABA (and best-effort register
the number) so inbound is delivered. Inbound: Meta posts a nested
entry/changes/value/messages webhook to the scoped route, verified with
X-Hub-Signature-256 (HMAC of the raw body with the app secret — one app, so the
secret is deployment-level, not per-connection). GET subscription verification is
answered by meta_verify() from the webhook route.
"""

import hashlib
import hmac
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
    WebhookVerificationError,
    lower_headers,
    split_composite_id,
)
from .meta_onboarding import (
    MetaOnboardingError,
    exchange_code,
    register_number,
    subscribe_app,
)

log = logging.getLogger("comm.meta")


def parse_meta_webhook(payload: bytes, phone_number_id: str) -> list[InboundMessage]:
    data = json.loads(payload)
    out: list[InboundMessage] = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            inbox = value.get("metadata", {}).get("phone_number_id", phone_number_id)
            for m in value.get("messages", []):
                if m.get("type") != "text":
                    continue
                remote = m.get("from")
                message_id = m.get("id")
                if not remote or not message_id:
                    continue
                out.append(
                    InboundMessage(
                        external_event_id=message_id,
                        provider_inbox_id=inbox,
                        provider_message_id=f"{remote}:{message_id}",
                        provider_thread_id=remote,
                        sender_address=remote,
                        recipients=[{"address": inbox}],
                        text=m.get("text", {}).get("body"),
                        chat_type="whatsapp",
                    )
                )
    return out


class MetaWhatsAppProvider:
    name = "meta-whatsapp"
    channel = "whatsapp"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    # Onboarding is via the Embedded Signup route, not the generic connect body.
    connect_credentials: tuple[str, ...] = ()

    def __init__(
        self,
        app_secret: str = "",
        verify_token: str = "",
        phone_number_id: str = "",
        access_token: str = "",
        register_pin: str = "000000",
        base_url: str = "https://graph.facebook.com/v21.0",
    ) -> None:
        self._app_secret = app_secret
        self.verify_token = verify_token
        self._default_phone_number_id = phone_number_id
        self._default_access_token = access_token
        self._register_pin = register_pin
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def _creds(self, credentials: Mapping[str, str] | None) -> tuple[str, str]:
        """Resolve (phone_number_id, access_token): per-connection first, else the
        deployment fallback. Raises if neither is configured."""
        creds = credentials or {}
        phone_number_id = creds.get("phone_number_id") or self._default_phone_number_id
        access_token = creds.get("access_token") or self._default_access_token
        if not (phone_number_id and access_token):
            raise ValueError(
                "meta-whatsapp needs phone_number_id + access_token "
                "(per-connection credentials or COMM_META_WA_* fallback)"
            )
        return phone_number_id, access_token

    def exchange_signup_code(self, app_id: str, app_secret: str, code: str) -> str:
        """Turn an Embedded Signup authorization code into a business access token."""
        return exchange_code(self._client, app_id, app_secret, code)

    def _deliver(self, credentials, to_number: str, text: str) -> SendResult:
        phone_number_id, access_token = self._creds(credentials)
        r = self._client.post(
            f"/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "text",
                "text": {"body": text},
            },
        )
        r.raise_for_status()
        wamid = r.json()["messages"][0]["id"]
        return SendResult(provider_message_id=f"{to_number}:{wamid}", provider_thread_id=to_number)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        creds = request.credentials or {}
        phone_number_id, access_token = self._creds(creds)
        waba_id = creds.get("waba_id")
        if waba_id:
            # Onboarded number: wire it up so inbound reaches us. subscribe_app is
            # required (no subscription = no webhooks); registration is best-effort
            # because Embedded Signup often pre-registers the number.
            subscribe_app(self._client, waba_id, access_token)
            try:
                register_number(self._client, phone_number_id, access_token, self._register_pin)
            except MetaOnboardingError:
                log.warning("meta register_number skipped for %s", phone_number_id, exc_info=True)
        return ProvisionResult(address=phone_number_id, provider_resource_id=phone_number_id)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._deliver(credentials, message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _ = split_composite_id(provider_message_id)
        return self._deliver(credentials, remote_number, message.text or "")

    def meta_verify(self, params: Mapping[str, str]) -> str | None:
        """Answer Meta's GET subscription challenge; None if it doesn't match.

        An unset verify_token must never validate (fail-closed) — otherwise an
        unconfigured provider would echo the challenge for any (even empty) token.
        """
        received = params.get("hub.verify_token") or ""
        if (
            params.get("hub.mode") == "subscribe"
            and self.verify_token
            and hmac.compare_digest(received, self.verify_token)
        ):
            return params.get("hub.challenge")
        return None

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        if self._app_secret:
            received = lower_headers(headers).get("x-hub-signature-256", "")
            expected = "sha256=" + hmac.new(
                self._app_secret.encode(), payload, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(received, expected):
                raise WebhookVerificationError("Meta signature mismatch")
        # One Meta app fans in every WABA's inbound to this one unscoped route, so
        # there are no per-connection credentials here. The real phone_number_id
        # rides in the payload's metadata (parse_meta_webhook reads it and routes by
        # it); the arg is only a fallback, so never require _creds - a pure
        # multi-tenant deployment has no COMM_META_WA_* number and must not 500.
        fallback = (credentials or {}).get("phone_number_id") or self._default_phone_number_id
        return parse_meta_webhook(payload, fallback or "")
