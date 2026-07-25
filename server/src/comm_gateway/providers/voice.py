"""Voice (phone call) adapter on Twilio Voice.

The telephony/orchestration layer for a Boardy-style calling agent: a
voice-capable number that can PLACE outbound calls and RECEIVE inbound ones,
normalized into our event model. The realtime speech loop (STT -> LLM -> TTS)
lives OUTSIDE the gateway: Twilio ConversationRelay (the default; Vapi/Retell
are drop-in alternatives) hosts a WebSocket that the call's audio is streamed
to. This provider never touches audio — it hands each call off to that external
endpoint via a `<Connect><ConversationRelay url=.../></Connect>` TwiML.

Contract shape mirrors the SMS adapter: the connection address is the agent's
Twilio number, provider_thread_id is the remote party's E.164 number, and
provider_message_id is "{remote}:{CallSid}" so a call routes without a lookup.

Number commissioning: if COMM_VOICE_FROM_NUMBER is set, every connection shares
that one line (fine for a trial or a single-number deployment). Otherwise each
connection PROVISIONS ITS OWN voice-capable number — search available -> buy ->
configure the inbound VoiceUrl — so each agent identity gets a dedicated line.
release() hands the number back so billing stops when a connection is torn down.
"""

from collections.abc import Mapping
from urllib.parse import parse_qs
from xml.sax.saxutils import quoteattr

import httpx

from ._twilio_sig import verify_twilio_webhook
from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
)

# Placeholder body recorded for the inbound-call event. The turn-by-turn
# transcript is the realtime layer's concern; here we only log that a call came
# in so the gateway has a conversation/event to anchor to.
INCOMING_CALL_TEXT = "[incoming call]"


def connectrelay_twiml(conversationrelay_url: str) -> str:
    """The TwiML that connects a call to the external realtime AI endpoint.

    A voice webhook must be answered with TwiML; a route/handler returns this
    string to bridge the live call to the ConversationRelay WebSocket. It is also
    handed to the Calls API inline (the `Twiml` param) when placing an outbound
    call, so no separate publicly-hosted TwiML endpoint is required.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f"<ConversationRelay url={quoteattr(conversationrelay_url)}/>"
        "</Connect></Response>"
    )


def parse_voice_webhook(payload: bytes, agent_number: str) -> list[InboundMessage]:
    """Normalize a Twilio inbound-voice webhook into an incoming-call event."""
    form = {k: v[0] for k, v in parse_qs(payload.decode()).items()}
    if not form.get("CallSid"):
        return []
    remote = form.get("From", "")
    to_number = form.get("To", agent_number)
    return [
        InboundMessage(
            external_event_id=form["CallSid"],
            provider_inbox_id=to_number,
            provider_message_id=f"{remote}:{form['CallSid']}",
            provider_thread_id=remote,
            sender_address=remote,
            recipients=[{"address": to_number}],
            text=INCOMING_CALL_TEXT,
            chat_type="voice",
        )
    ]


class VoiceProvider:
    name = "twilio-voice"
    channel = "voice"
    # A voice line receives calls and can cold-call. It does not carry a
    # text-message SEND/REPLY semantics — turns are handled by the realtime
    # layer, not this provider — so those capabilities are deliberately absent.
    capabilities = frozenset({Capability.RECEIVE, Capability.INITIATE})

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str = "",
        conversationrelay_url: str = "",
        provision_country: str = "US",
        provision_area_code: str = "",
        inbound_webhook_url: str = "",
        base_url: str = "https://api.twilio.com",
        verify_url: str = "",
    ) -> None:
        if not (account_sid and auth_token):
            raise ValueError(
                "COMM_TWILIO_ACCOUNT_SID and COMM_TWILIO_AUTH_TOKEN are required "
                "for the twilio-voice provider"
            )
        if not conversationrelay_url:
            raise ValueError(
                "COMM_VOICE_CONVERSATIONRELAY_URL is required for the twilio-voice "
                "provider (the external realtime-AI WebSocket the call connects to)"
            )
        self._sid = account_sid
        self._auth_token = auth_token
        self._fixed_from = from_number
        self._relay_url = conversationrelay_url
        self._country = provision_country
        self._area_code = provision_area_code
        self._inbound_webhook_url = inbound_webhook_url
        # URL Twilio POSTs the inbound voice webhook to; verifying the signature
        # needs the exact URL.
        self._verify_url = verify_url or inbound_webhook_url
        self._client = httpx.Client(
            base_url=base_url, auth=(account_sid, auth_token), timeout=30.0
        )

    def _acct(self, path: str) -> str:
        return f"/2010-04-01/Accounts/{self._sid}{path}"

    def _place_call(self, from_number: str, to_number: str) -> SendResult:
        """Ring `to_number` and, on answer, bridge it to the realtime agent."""
        response = self._client.post(
            self._acct("/Calls.json"),
            data={
                "From": from_number,
                "To": to_number,
                "Twiml": connectrelay_twiml(self._relay_url),
            },
        )
        response.raise_for_status()
        data = response.json()
        return SendResult(
            provider_message_id=f"{to_number}:{data['sid']}",
            provider_thread_id=to_number,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        if self._fixed_from:
            # Shared-number mode: one voice line for the whole deployment.
            return ProvisionResult(
                address=self._fixed_from, provider_resource_id=self._fixed_from
            )
        # Per-agent commissioning: search for a voice-capable number and buy it.
        search = {"VoiceEnabled": "true", "PageSize": 1}
        if self._area_code:
            search["AreaCode"] = self._area_code
        avail = self._client.get(
            self._acct(f"/AvailablePhoneNumbers/{self._country}/Local.json"), params=search
        )
        avail.raise_for_status()
        options = avail.json().get("available_phone_numbers", [])
        if not options:
            raise RuntimeError(f"no available Twilio voice numbers in {self._country}")
        buy = {"PhoneNumber": options[0]["phone_number"]}
        if self._inbound_webhook_url:
            buy["VoiceUrl"] = self._inbound_webhook_url
        bought = self._client.post(self._acct("/IncomingPhoneNumbers.json"), data=buy)
        bought.raise_for_status()
        record = bought.json()
        # address+resource_id = the E.164 (for inbound matching); pod_id = the SID
        # (for release). The SID never leaves this package.
        return ProvisionResult(
            address=record["phone_number"],
            provider_resource_id=record["phone_number"],
            provider_pod_id=record["sid"],
        )

    def release(self, provider_resource_id: str, provider_pod_id: str | None) -> None:
        """Hand a commissioned number back to Twilio (stops billing)."""
        if self._fixed_from or not provider_pod_id:
            return  # shared/trial number — never release it out from under others
        self._client.delete(self._acct(f"/IncomingPhoneNumbers/{provider_pod_id}.json"))

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        # A "message" on a voice line is a call: ring the recipient and bridge
        # them to the realtime agent. Text turns are the realtime layer's job.
        return self._place_call(provider_inbox_id, message.to[0])

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        # No text-reply semantics on voice; call the party back instead.
        remote_number, _, _ = provider_message_id.partition(":")
        return self._place_call(provider_inbox_id, remote_number)

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._place_call(provider_inbox_id, recipient)

    def inbound_twiml(self) -> str:
        """The TwiML a voice-webhook route returns to connect the live call.

        Exposed so the inbound route can answer Twilio with the ConversationRelay
        bridge; parsing the call into our records (parse_webhook) is separate.
        """
        return connectrelay_twiml(self._relay_url)

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        verify_twilio_webhook(self._auth_token, self._verify_url, payload, headers)
        return parse_voice_webhook(payload, self._fixed_from)
