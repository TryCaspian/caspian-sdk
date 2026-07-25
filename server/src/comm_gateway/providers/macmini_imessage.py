"""Self-hosted iMessage adapter via a BlueBubbles server on a Mac mini.

Apple has no iMessage API. This is the self-hosted path: a Mac mini signed into
an Apple ID runs BlueBubbles (https://bluebubbles.app), a REST server that drives
the real Messages app via AppleScript / Private API. We talk to that server.

Because a deployment owns exactly ONE Mac mini / Apple ID, this is a
deployment-level integration like SES — the BlueBubbles URL, password, and the
signed-in handle are config, not per-connection credentials. Pick one iMessage
provider per deployment via COMM_PROVIDERS.

BlueBubbles REST (self-hosted, default http://<macmini>:1234, auth via a
`password` query param):
  - Send: POST /api/v1/message/text?password=<pw> with
    {"chatGuid": "iMessage;-;<address>", "tempGuid": "<uuid>",
     "message": "<text>", "method": "apple-script"}. The response's `data`
    carries the created message `guid`.
  - Inbound: BlueBubbles POSTs a webhook on new messages:
    {"type": "new-message", "data": {"guid", "text", "isFromMe",
     "handle": {"address"}, "chats": [{"guid": "iMessage;-;<address>"}]}}.
    We ignore isFromMe (our own echoes) and, when a secret is configured,
    verify a shared-secret header (opt-in, like the Twilio/Meta providers).

A DM chatGuid is `iMessage;-;+15551234567` or `iMessage;-;email@example.com`.
provider_message_id is "{chatGuid}:{guid}" so a reply routes without a lookup
(the chatGuid has no ':' so partition(':') recovers it cleanly).
"""

import hmac
import json
import uuid
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

SECRET_HEADER = "x-bluebubbles-secret"


def chat_guid_for(address: str) -> str:
    """Build the iMessage DM chatGuid for a handle (phone or email).

    Idempotent: if the caller already passed a full `iMessage;-;...` guid we
    leave it alone, so reply()/initiate() can hand through either form.
    """
    address = address.strip()
    if address.startswith("iMessage;"):
        return address
    return f"iMessage;-;{address}"


def parse_new_message(payload: dict, inbox_handle: str) -> list[InboundMessage]:
    """Normalize a BlueBubbles `new-message` webhook into our schema.

    Skips messages we sent (isFromMe) so an agent never echoes its own text.
    """
    if payload.get("type") != "new-message":
        return []
    data = payload.get("data") or {}
    if data.get("isFromMe"):
        return []
    guid = data.get("guid")
    if not guid:
        return []
    chats = data.get("chats") or []
    chat_guid = chats[0].get("guid") if chats else None
    handle = data.get("handle") or {}
    sender = handle.get("address")
    # Fall back to deriving the thread from the sender for a DM without a chat.
    if not chat_guid and sender:
        chat_guid = chat_guid_for(sender)
    if not chat_guid:
        return []
    return [
        InboundMessage(
            external_event_id=guid,
            provider_inbox_id=inbox_handle,
            provider_message_id=f"{chat_guid}:{guid}",
            provider_thread_id=chat_guid,
            sender_address=sender,
            recipients=[{"address": inbox_handle}],
            text=data.get("text"),
            chat_type="imessage",
        )
    ]


class MacMiniIMessageProvider:
    name = "macmini-imessage"
    channel = "imessage"
    # Deployment-owned Mac mini: no per-connection credentials to collect.
    connect_credentials: tuple[str, ...] = ()
    # Real iMessage can DM any known handle, so
    # cold-start (INITIATE) is offered alongside receive/reply/send.
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
        }
    )

    def __init__(
        self,
        base_url: str,
        password: str,
        handle: str,
        webhook_secret: str = "",
    ) -> None:
        if not (base_url and password and handle):
            raise ValueError(
                "COMM_MACMINI_BLUEBUBBLES_URL, COMM_MACMINI_BLUEBUBBLES_PASSWORD and "
                "COMM_MACMINI_IMESSAGE_HANDLE are required for the macmini-imessage provider"
            )
        self._handle = handle
        self._webhook_secret = webhook_secret
        # The password rides on every request as a query param (BlueBubbles auth).
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            params={"password": password},
            timeout=30.0,
        )

    def _post_text(self, chat_guid: str, text: str) -> SendResult:
        r = self._client.post(
            "/api/v1/message/text",
            json={
                "chatGuid": chat_guid,
                "tempGuid": str(uuid.uuid4()),
                "message": text,
                "method": "apple-script",
            },
        )
        r.raise_for_status()
        data = r.json().get("data") or {}
        guid = data.get("guid", "")
        return SendResult(
            provider_message_id=f"{chat_guid}:{guid}",
            provider_thread_id=chat_guid,
        )

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # The address is the Apple ID / number the Mac mini is signed into
        # (config). Connectivity validation is intentionally deferred so connect
        # never blocks on the Mac mini being reachable.
        return ProvisionResult(address=self._handle, provider_resource_id=self._handle)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        return self._post_text(chat_guid_for(message.to[0]), message.text or "")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        # The chatGuid is the part before the message guid; it carries no ':',
        # so a left partition recovers it even when the guid does (e.g. "p:0/...").
        chat_guid, _, _ = provider_message_id.partition(":")
        return self._post_text(chat_guid, message.text or "")

    def initiate(
        self, provider_inbox_id: str, recipient: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        return self._post_text(chat_guid_for(recipient), message.text or "")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        if self._webhook_secret:
            received = {k.lower(): v for k, v in headers.items()}.get(SECRET_HEADER, "")
            if not hmac.compare_digest(received, self._webhook_secret):
                raise WebhookVerificationError("BlueBubbles webhook secret mismatch")
        return parse_new_message(json.loads(payload), self._handle)
