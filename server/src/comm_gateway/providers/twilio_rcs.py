"""RCS adapter on Twilio (RCS Business Messaging).

RCS is the sanctioned rich channel that now reaches iPhones too (iOS 18.1+). It
sends through a Twilio Messaging Service that has an RCS sender in its pool;
Twilio routes RCS and falls back to SMS automatically. Needs a registered RCS
agent/brand on the messaging service — a console setup step, not code.
"""

from collections.abc import Mapping
from urllib.parse import parse_qs

import httpx

from ._twilio_sig import verify_twilio_webhook
from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    split_composite_id,
)


def parse_rcs_webhook(payload: bytes, agent: str) -> list[InboundMessage]:
    form = {k: v[0] for k, v in parse_qs(payload.decode()).items()}
    if not form.get("MessageSid") or form.get("Body") is None:
        return []
    remote = form["From"]
    return [
        InboundMessage(
            external_event_id=form["MessageSid"],
            provider_inbox_id=form.get("To", agent),
            provider_message_id=f"{remote}:{form['MessageSid']}",
            provider_thread_id=remote,
            sender_address=remote,
            recipients=[{"address": form.get("To", agent)}],
            text=form["Body"],
            chat_type="rcs",
        )
    ]


class TwilioRcsProvider:
    name = "twilio-rcs"
    channel = "rcs"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        messaging_service_sid: str,
        base_url: str = "https://api.twilio.com",
        verify_url: str = "",
    ) -> None:
        if not (account_sid and auth_token and messaging_service_sid):
            raise ValueError(
                "COMM_TWILIO_ACCOUNT_SID, COMM_TWILIO_AUTH_TOKEN and "
                "COMM_TWILIO_RCS_MESSAGING_SERVICE_SID are required for twilio-rcs"
            )
        self._sid = account_sid
        self._auth_token = auth_token
        self._verify_url = verify_url
        self._service = messaging_service_sid
        self._client = httpx.Client(
            base_url=base_url, auth=(account_sid, auth_token), timeout=30.0
        )

    def _send(self, to_number: str, text: str) -> SendResult:
        r = self._client.post(
            f"/2010-04-01/Accounts/{self._sid}/Messages.json",
            data={"MessagingServiceSid": self._service, "To": to_number, "Body": text},
        )
        r.raise_for_status()
        return SendResult(
            provider_message_id=f"{to_number}:{r.json()['sid']}", provider_thread_id=to_number
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(address=self._service, provider_resource_id=self._service)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._send(message.to[0], message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        remote_number, _ = split_composite_id(provider_message_id)
        return self._send(remote_number, message.text or "")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        verify_twilio_webhook(self._auth_token, self._verify_url, payload, headers)
        return parse_rcs_webhook(payload, self._service)
