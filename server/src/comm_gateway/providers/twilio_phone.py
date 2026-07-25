"""Phone (SMS) adapter on Twilio.

Bring-your-own model: the developer supplies THEIR OWN Twilio account_sid +
auth_token + a number they own (from_number). Every API call is signed with the
connection's own credentials, so their account handles billing, 10DLC and
outbound-SMS enablement - Caspian just relays. connect_credentials makes those
required at connect time (same pattern as Telegram/Slack/X).

A deployment-level account (env) may still be configured as a fallback for a
Caspian-managed number path, but BYO is the public, open-sourceable model and
needs no Caspian secrets.

Contract: the connection address is the agent's Twilio number,
provider_thread_id is the remote party's E.164, provider_message_id is
"{remote}:{MessageSid}" so a reply routes without a lookup. Inbound arrives on
the scoped webhook /internal/providers/twilio/webhooks/{from_number}, verified
with the connection's own auth_token.
"""

from collections.abc import Mapping
from urllib.parse import parse_qs

import httpx

from ._twilio_sig import verify_twilio_signature
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


def parse_form_webhook(payload: bytes, agent_number: str) -> list[InboundMessage]:
    form = {k: v[0] for k, v in parse_qs(payload.decode()).items()}
    if not form.get("MessageSid") or form.get("Body") is None:
        return []
    remote = form["From"]
    return [
        InboundMessage(
            external_event_id=form["MessageSid"],
            provider_inbox_id=form.get("To", agent_number),
            provider_message_id=f"{remote}:{form['MessageSid']}",
            provider_thread_id=remote,
            sender_address=remote,
            recipients=[{"address": form.get("To", agent_number)}],
            text=form["Body"],
            chat_type="sms",
        )
    ]


class TwilioPhoneProvider:
    name = "twilio"
    channel = "phone"
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
            Capability.OTP,  # best-effort, same as any CPaaS line
        }
    )
    # BYO: the developer brings their own Twilio account + number.
    connect_credentials: tuple[str, ...] = ("account_sid", "auth_token", "from_number")

    def __init__(
        self,
        account_sid: str = "",
        auth_token: str = "",
        from_number: str = "",
        provision_country: str = "US",
        provision_area_code: str = "",
        inbound_webhook_url: str = "",
        base_url: str = "https://api.twilio.com",
        verify_url: str = "",
    ) -> None:
        # Deployment fallback (optional). BYO connections carry their own creds, so
        # the provider is constructable with none of these set.
        self._sid = account_sid
        self._auth_token = auth_token
        self._fixed_from = from_number
        self._country = provision_country
        self._area_code = provision_area_code
        self._inbound_webhook_url = inbound_webhook_url
        self._verify_url = verify_url or inbound_webhook_url
        self._base_url = base_url
        # Auth is supplied PER REQUEST from the connection's creds (BYO), so the
        # shared client carries no fixed auth.
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def _creds(self, credentials) -> tuple[str, str, str]:
        """(account_sid, auth_token, from_number) for this connection, falling back
        to the deployment account when a field is absent."""
        c = credentials or {}
        return (
            c.get("account_sid") or self._sid,
            c.get("auth_token") or self._auth_token,
            c.get("from_number") or self._fixed_from,
        )

    def _send_sms(self, credentials, from_number: str, to_number: str, text: str) -> SendResult:
        sid, token, _ = self._creds(credentials)
        response = self._client.post(
            f"/2010-04-01/Accounts/{sid}/Messages.json",
            data={"From": from_number, "To": to_number, "Body": text},
            auth=(sid, token),
        )
        response.raise_for_status()
        data = response.json()
        return SendResult(
            provider_message_id=f"{to_number}:{data['sid']}",
            provider_thread_id=to_number,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # BYO (or shared-number): the number IS the connection's from_number - no
        # commissioning. address == resource_id so inbound routes by the number.
        _, _, from_number = self._creds(request.credentials)
        if from_number:
            return ProvisionResult(address=from_number, provider_resource_id=from_number)
        raise ValueError(
            "twilio needs a from_number (bring your own Twilio number at connect)"
        )

    def release(self, provider_resource_id: str, provider_pod_id: str | None) -> None:
        # BYO numbers belong to the developer - never release them.
        return

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        _, _, from_number = self._creds(credentials)
        return self._send_sms(credentials, from_number or provider_inbox_id,
                              message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        _, _, from_number = self._creds(credentials)
        remote_number, _ = split_composite_id(provider_message_id)
        return self._send_sms(credentials, from_number or provider_inbox_id,
                              remote_number, message.text or "")

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        _, _, from_number = self._creds(credentials)
        return self._send_sms(credentials, from_number or provider_inbox_id,
                              recipient, message.text or "")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        sid, token, from_number = self._creds(credentials)
        # Verify with THIS connection's auth token. The exact public URL Twilio was
        # configured with rides in credentials (added by the scoped webhook route);
        # fall back to the deployment verify_url.
        url = (credentials or {}).get("_webhook_url") or self._verify_url
        if token and url:
            form = {k: v[0] for k, v in parse_qs(payload.decode()).items()}
            sig = lower_headers(headers).get("x-twilio-signature", "")
            if not verify_twilio_signature(token, url, form, sig):
                raise WebhookVerificationError("Twilio signature mismatch")
        return parse_form_webhook(payload, from_number)
