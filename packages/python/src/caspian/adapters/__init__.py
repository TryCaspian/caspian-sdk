"""Adapters package — channel packs. The only code that knows a platform exists.

Each adapter satisfies the AdapterPort protocol (parse / execute / overlap_key /
capabilities / verify / format). Adding a channel = a catalog row in
`caspian.catalog` plus an adapter here; REGISTRY, listen(), and bot-token
rules are derived from the row. Capabilities are never listed locally.
"""

from __future__ import annotations

from caspian.adapters.discord import DiscordAdapter
from caspian.adapters.email import EmailAdapter
from caspian.adapters.imessage import IMessageAdapter
from caspian.adapters.linear import LinearAdapter
from caspian.adapters.messenger import MessengerAdapter
from caspian.adapters.slack import SlackAdapter
from caspian.adapters.sms import SmsAdapter
from caspian.adapters.telegram import TelegramAdapter
from caspian.adapters.voice import VoiceAdapter
from caspian.adapters.whatsapp import WhatsAppAdapter
from caspian.adapters.x import XAdapter
from caspian.catalog import CHANNELS

# Registry: channel name → adapter class. Used by the facade/runner to look up
# the right pack for a connection.
_ADAPTERS: dict[str, type] = {
    "telegram": TelegramAdapter,
    "slack": SlackAdapter,
    "discord": DiscordAdapter,
    "email": EmailAdapter,
    "whatsapp": WhatsAppAdapter,
    "messenger": MessengerAdapter,
    "sms": SmsAdapter,
    "voice": VoiceAdapter,
    "imessage": IMessageAdapter,
    "x": XAdapter,
    "linear": LinearAdapter,
}

if set(_ADAPTERS) != set(CHANNELS):
    raise RuntimeError(
        f"adapter map {sorted(_ADAPTERS)} != catalog {sorted(CHANNELS)}"
    )

REGISTRY: dict[str, type] = dict(_ADAPTERS)


def get_adapter(channel: str) -> object:
    """Instantiate the adapter for a channel name. Raises KeyError if unknown."""
    return REGISTRY[channel]()


__all__ = [
    "REGISTRY",
    "DiscordAdapter",
    "EmailAdapter",
    "IMessageAdapter",
    "LinearAdapter",
    "MessengerAdapter",
    "SlackAdapter",
    "SmsAdapter",
    "TelegramAdapter",
    "VoiceAdapter",
    "WhatsAppAdapter",
    "XAdapter",
    "get_adapter",
]
