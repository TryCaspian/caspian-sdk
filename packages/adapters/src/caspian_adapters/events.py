"""Gateway-facing helpers for normalized adapter events.

Adapters return normalized inbound objects, not gateway records. The gateway is
still responsible for routing by provider_inbox_id, finding/creating the
conversation and source message, then appending the event-stream record. These
helpers keep the type-to-event-name mapping in one place.
"""

from .base import InboundCommand, InboundEvent, InboundMessage, InboundReaction

MESSAGE_RECEIVED = "message.received"
REACTION_RECEIVED = "reaction.received"
COMMAND_RECEIVED = "command.received"


def event_type(event: InboundEvent) -> str:
    """Return the event-stream type for a normalized adapter event."""
    if isinstance(event, InboundMessage):
        return MESSAGE_RECEIVED
    if isinstance(event, InboundReaction):
        return REACTION_RECEIVED
    if isinstance(event, InboundCommand):
        return COMMAND_RECEIVED
    raise TypeError(f"unknown inbound event type: {type(event).__name__}")


def event_payload(event: InboundEvent) -> dict:
    """Return the provider-level payload for a normalized adapter event.

    Gateway code should enrich this with connection_id, customer_id, agent_id,
    conversation_id, source_message, and SDK-facing sender fields after it
    resolves provider_inbox_id/provider_thread_id/provider_message_id.
    """
    return event.to_payload()


__all__ = [
    "COMMAND_RECEIVED",
    "MESSAGE_RECEIVED",
    "REACTION_RECEIVED",
    "event_payload",
    "event_type",
]
