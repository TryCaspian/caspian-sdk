"""Internal provider contract.

Everything above this boundary speaks our schema; everything below it speaks
the provider's. No provider type may leak out of this package.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Protocol


class WebhookVerificationError(Exception):
    """Raised when an inbound webhook fails signature verification."""


def split_composite_id(mid: str) -> tuple[str, str]:
    """Split a `head:tail` composite provider_message_id into its two parts."""
    head, _, tail = mid.partition(":")
    return head, tail


def lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Case-fold header names for case-insensitive lookup."""
    return {k.lower(): v for k, v in headers.items()}


class Capability:
    """Named capabilities a transport may or may not support.

    Channels are not uniform. The gateway checks a provider's declared
    capabilities before offering an operation, so callers get an honest
    422 instead of a silent failure when a transport can't do something.
    """

    RECEIVE = "receive"  # inbound messages after the connection exists
    REPLY = "reply"  # reply to a specific inbound message
    SEND = "send"  # proactively message an existing conversation
    INITIATE = "initiate"  # cold-start a brand new conversation (SMS, user-account, ...)
    GROUP_VISIBILITY = "group_visibility"  # see all group messages, not just @mentions
    EDIT_INBOUND = "edit_inbound"  # receive edits to inbound messages
    BACKFILL = "backfill"  # fetch history from before the connection existed
    PRESENCE = "presence"  # online/last-seen/typing of the other party
    READ_RECEIPTS = "read_receipts"  # know when our message was read
    AUTO_JOIN = "auto_join"  # join a group/channel on our own
    SEE_BOTS = "see_bots"  # receive messages authored by other bots
    SECRET_CHATS = "secret_chats"  # end-to-end secret chats
    OTP = "otp"  # receives 3rd-party codes (real-SIM reliable, CPaaS best-effort); gateway extracts
    INTERACTIONS = "interactions"  # button taps / message components round-trip back to the agent
    MEDIA = "media"  # send and/or receive file attachments (images, documents, ...)
    REACTIONS = "reactions"  # add emoji reactions and receive reaction events
    EDIT_OUTBOUND = "edit_outbound"  # edit messages we previously sent (streaming post+edit)


# Every valid capability string, for validating a connection's manifest.
ALL_CAPABILITIES = frozenset(
    v for k, v in vars(Capability).items() if not k.startswith("_") and isinstance(v, str)
)

# Always granted; a connection never has to ask for the basics and they are
# never the risky operations a manifest exists to gate.
BASELINE_CAPABILITIES = frozenset({Capability.RECEIVE, Capability.REPLY})


@dataclass(frozen=True)
class ProvisionRequest:
    connection_id: str
    customer_id: str
    agent_id: str
    display_name: str | None = None
    credentials: dict = field(default_factory=dict)
    domain: str | None = None  # verified custom domain to allocate the address on
    username: str | None = None  # exact local part (custom domains only)


@dataclass(frozen=True)
class ProvisionResult:
    address: str
    provider_resource_id: str
    provider_pod_id: str | None = None


@dataclass(frozen=True)
class OutboundMessage:
    text: str | None = None
    html: str | None = None
    subject: str | None = None
    to: tuple[str, ...] = ()
    # Provider-neutral rich blocks (see providers/blocks.py). Channels that render
    # natively (Slack/Discord/Telegram) consume this; others rely on the text/html
    # the gateway already flattened from these blocks, so this stays optional.
    blocks: tuple | None = None
    # File attachments to send alongside the message. Each item is a dict:
    # {"url": ...} or {"data": <base64>}, plus "mime_type"/"name"/"size". Channels
    # that carry files (Telegram, email, Slack, Discord) attach them; others append
    # any URL to the text so the link still reaches the recipient.
    media: tuple | None = None


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    provider_thread_id: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    external_event_id: str
    provider_inbox_id: str
    provider_message_id: str
    provider_thread_id: str
    sender_address: str | None = None
    sender_name: str | None = None
    recipients: list[dict] = field(default_factory=list)
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    chat_type: str | None = None  # "private" | "group" | "channel" | ...
    edited: bool = False
    auto_generated: bool = False  # auto-responder/bounce/no-reply; never auto-reply to these
    # What this inbound event IS. "message" is a normal message and is the default
    # so every existing provider is unchanged. "interaction" is a button tap;
    # "reaction" is an emoji reaction. The ingest queue maps this to the event type
    # (message.received / interaction.received / reaction.received) so the SDK can
    # subscribe to each independently.
    kind: str = "message"
    # For kind="interaction": {"value": <decoded callback value>,
    # "source_message_id": <provider_message_id of the message whose button was tapped>}.
    action: dict | None = None
    # For kind="reaction": {"emoji": ..., "source_message_id": ...,
    # "action": "added" | "removed"}.
    reaction: dict | None = None
    # File attachments received with a message (kind="message"). Each item is a
    # dict: {"url"|"data", "mime_type", "name", "size"}.
    media: list[dict] = field(default_factory=list)

    def to_payload(self) -> dict:
        return asdict(self)


class ChannelProvider(Protocol):
    """The contract every transport implements, regardless of channel.

    Optional, channel-specific operations are not part of this Protocol: the
    gateway calls them only on providers that support them (capability-gated, or
    an email-only route), so they stay optional rather than forcing every
    provider to stub them. Their signatures:

        initiate(provider_inbox_id, recipient, message) -> SendResult
        backfill(provider_inbox_id, thread_id, limit)   -> list[InboundMessage]
        send_test_email(provider_inbox_id, to, subject, text) -> InboundMessage | None
        release(provider_resource_id, provider_pod_id)  -> None  # deprovision a number
        react(provider_message_id, emoji, credentials)  -> None  # add an emoji reaction
        parse_interaction(payload, headers, credentials) -> list[InboundMessage]  # button taps
    """

    name: str
    channel: str
    capabilities: frozenset[str]
    # Credential field names a connect request must supply (e.g. a per-developer
    # bot token). Empty for transports we fully own, like email on our domain.
    connect_credentials: tuple[str, ...] = ()

    def provision(self, request: ProvisionRequest) -> ProvisionResult: ...

    def send(
        self,
        provider_inbox_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult: ...

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials: Mapping[str, str] | None = None,
    ) -> SendResult: ...

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials: Mapping[str, str] | None = None,
    ) -> list[InboundMessage]: ...
