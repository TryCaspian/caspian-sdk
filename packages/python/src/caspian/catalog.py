"""Channel catalog — one vocabulary for names, inbound, bot-token, capabilities.

Adapters, provision, listen, and streaming derive from these rows. Hosted-only
channels the gateway speaks (bluesky, …) are not rows; they stay open strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from caspian._compat import StrEnum

ChannelName = Literal[
    "telegram",
    "slack",
    "discord",
    "email",
    "whatsapp",
    "messenger",
    "sms",
    "voice",
    "imessage",
    "x",
    "linear",
]


class Capability(StrEnum):
    RECEIVE = "receive"
    REPLY = "reply"
    SEND = "send"
    MEDIA = "media"
    BUTTONS = "buttons"
    BLOCKS = "blocks"
    EMBEDS = "embeds"
    EDIT = "edit"
    DELETE = "delete"
    REACT = "react"
    TYPING = "typing"
    PIN = "pin"
    FORWARD = "forward"
    THREADING = "threading"
    MEMBERSHIP = "membership"
    MODALS = "modals"
    HISTORY = "history"
    DM = "dm"
    VOICE = "voice"
    TTS = "tts"
    RECEIPTS = "receipts"


class InboundMode(StrEnum):
    WEBHOOK = "webhook"
    SOCKET = "socket"
    POLL = "poll"


class SocketKind(StrEnum):
    DISCORD = "discord"
    SLACK = "slack"


class BotTokenWhen(StrEnum):
    ALWAYS = "always"
    SELF_HOST = "self-host"


@dataclass(frozen=True, slots=True)
class ChannelRow:
    inbound: frozenset[InboundMode]
    bot_token: BotTokenWhen
    capabilities: frozenset[Capability]
    socket: SocketKind | None = None


def _caps(*items: Capability) -> frozenset[Capability]:
    return frozenset(items)


CHANNELS: dict[ChannelName, ChannelRow] = {
    "telegram": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK, InboundMode.POLL}),
        bot_token=BotTokenWhen.ALWAYS,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
            Capability.BUTTONS,
            Capability.EDIT,
            Capability.DELETE,
            Capability.REACT,
            Capability.TYPING,
            Capability.PIN,
            Capability.FORWARD,
            Capability.THREADING,
            Capability.MEMBERSHIP,
        ),
    ),
    "slack": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK, InboundMode.SOCKET}),
        bot_token=BotTokenWhen.SELF_HOST,
        socket=SocketKind.SLACK,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
            Capability.BUTTONS,
            Capability.BLOCKS,
            Capability.REACT,
            Capability.EDIT,
            Capability.DELETE,
            Capability.THREADING,
            Capability.MODALS,
            Capability.HISTORY,
        ),
    ),
    "discord": ChannelRow(
        inbound=frozenset({InboundMode.SOCKET}),
        bot_token=BotTokenWhen.SELF_HOST,
        socket=SocketKind.DISCORD,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
            Capability.BUTTONS,
            Capability.EMBEDS,
            Capability.REACT,
            Capability.EDIT,
            Capability.DELETE,
            Capability.TYPING,
            Capability.MODALS,
            Capability.PIN,
        ),
    ),
    "email": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
            Capability.THREADING,
        ),
    ),
    "whatsapp": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
            Capability.BUTTONS,
            Capability.REACT,
            Capability.RECEIPTS,
        ),
    ),
    "messenger": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
            Capability.BUTTONS,
            Capability.TYPING,
        ),
    ),
    "sms": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
        ),
    ),
    "voice": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.SEND,
            Capability.VOICE,
            Capability.TTS,
        ),
    ),
    "imessage": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.MEDIA,
        ),
    ),
    "x": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.SEND,
            Capability.REPLY,
            Capability.DM,
        ),
    ),
    "linear": ChannelRow(
        inbound=frozenset({InboundMode.WEBHOOK}),
        bot_token=BotTokenWhen.SELF_HOST,
        capabilities=_caps(
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.THREADING,
        ),
    ),
}


def socket_channels() -> tuple[ChannelName, ...]:
    """Channels whose inbound includes a held-open socket (`listen()`)."""
    return tuple(name for name, row in CHANNELS.items() if row.socket is not None)


def needs_bot_token(channel: str, via: str) -> bool:
    """Whether this add() needs a bot_token. Unknown hosted channels do not."""
    row = CHANNELS.get(channel)  # type: ignore[arg-type]
    if row is None:
        return via == "self-host"
    if row.bot_token is BotTokenWhen.ALWAYS:
        return True
    return via == "self-host"


def capabilities_of(channel: str) -> frozenset[str]:
    """Adapter-facing capability strings, derived from the catalog row."""
    return frozenset(str(c) for c in CHANNELS[channel].capabilities)  # type: ignore[index]
