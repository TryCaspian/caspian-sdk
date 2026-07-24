"""Internal provider contract.

Everything above this boundary speaks our schema; everything below it speaks
the provider's. No provider type may leak out of this package.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Protocol, Union


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
    ATTACHMENTS = "attachments"  # send/receive file attachments (image, document, voice, …)
    REACTIONS = "reactions"  # add/remove emoji reactions
    COMMANDS = "commands"  # slash commands or bot commands


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
class Attachment:
    """A file carried alongside a message — image, document, voice note, etc.

    ``url`` is a directly fetchable link when the provider gives one (Discord).
    When the provider only hands back an opaque handle (Telegram's ``file_id``),
    ``provider_file_id`` carries it and ``url`` stays ``None`` until it is
    resolved downstream. The remaining fields are filled in when the provider
    reports them.
    """

    url: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    provider_file_id: str | None = None


@dataclass(frozen=True)
class OutboundMessage:
    text: str | None = None
    html: str | None = None
    subject: str | None = None
    to: tuple[str, ...] = ()
    attachments: list[Attachment] = field(default_factory=list)


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
    attachments: list[Attachment] = field(default_factory=list)

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class InboundReaction:
    """A normalized emoji reaction event (add or remove) from any channel."""

    external_event_id: str
    provider_inbox_id: str
    emoji: str
    action: str  # "added" | "removed"
    source_provider_message_id: str
    sender_address: str | None = None
    sender_name: str | None = None

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class InboundCommand:
    """A normalized slash command or bot command invocation from any channel."""

    external_event_id: str
    provider_inbox_id: str
    provider_message_id: str
    provider_thread_id: str
    command: str  # e.g. "/start", "deploy"
    args: str | None = None
    text: str | None = None
    sender_address: str | None = None
    sender_name: str | None = None
    chat_type: str | None = None

    def to_payload(self) -> dict:
        return asdict(self)


InboundEvent = Union["InboundMessage", "InboundReaction", "InboundCommand"]


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
    ) -> list[InboundEvent]: ...
