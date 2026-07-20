"""Caspian channel adapters: one interface, every channel humans use.

Each adapter turns a platform (Slack, Discord, Telegram, Instagram, email, ...)
into the same small surface: provision / send / reply / parse_webhook, with
capability negotiation. Bring your own platform credentials.
"""

from .base import (
    ALL_CAPABILITIES,
    BASELINE_CAPABILITIES,
    Capability,
    ChannelProvider,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)
from .config import Settings
from .registry import PLUGIN_GROUP, build_providers

__all__ = [
    "ALL_CAPABILITIES",
    "BASELINE_CAPABILITIES",
    "Capability",
    "ChannelProvider",
    "InboundMessage",
    "OutboundMessage",
    "PLUGIN_GROUP",
    "ProvisionRequest",
    "ProvisionResult",
    "SendResult",
    "Settings",
    "WebhookVerificationError",
    "build_providers",
]
