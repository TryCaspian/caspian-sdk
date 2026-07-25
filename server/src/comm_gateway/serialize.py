"""JSON-safe serializers shared by API responses and stored events.

These dicts are the public wire shapes. Provider identifiers must never
appear here.
"""

from . import models


def customer_out(c: models.Customer) -> dict:
    return {"id": c.id, "name": c.name, "created_at": c.created_at.isoformat()}


def agent_out(a: models.Agent) -> dict:
    return {"id": a.id, "name": a.name, "created_at": a.created_at.isoformat()}


def connection_out(c: models.Connection) -> dict:
    return {
        "id": c.id,
        "channel": c.channel,
        "capabilities": c.capabilities or [],
        "status": c.status,
        "address": c.address,
        "customer_id": c.customer_id,
        "agent_id": c.agent_id,
        "error": c.error,
        "created_at": c.created_at.isoformat(),
    }


def conversation_out(c: models.Conversation) -> dict:
    return {
        "id": c.id,
        "connection_id": c.connection_id,
        "subject": c.subject,
        "created_at": c.created_at.isoformat(),
    }


def message_out(m: models.Message) -> dict:
    sender = None
    if m.sender_address:
        sender = {"address": m.sender_address, "name": m.sender_name}
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "connection_id": m.connection_id,
        "channel": m.channel,
        "direction": m.direction,
        "status": m.status,
        "sender": sender,
        "recipients": m.recipients or [],
        "subject": m.subject,
        "text": m.text,
        "html": m.html,
        "media": m.media or [],
        "chat_type": m.chat_type,
        "edited": m.edited,
        "auto_generated": m.auto_generated,
        "created_at": m.created_at.isoformat(),
    }


def event_out(e: models.Event) -> dict:
    return {
        "id": e.id,
        "seq": e.seq,
        "type": e.type,
        "occurred_at": e.created_at.isoformat(),
        "data": e.payload,
    }
