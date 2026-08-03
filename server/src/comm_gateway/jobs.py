"""Durable job queue and handlers.

Jobs are rows in outbox_jobs. The worker (inline thread or comm-worker
process) claims pending jobs in order and retries failures up to
MAX_ATTEMPTS before marking them failed.
"""

import hashlib
import hmac
import json
import logging

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .crypto import read_credentials, write_credentials
from .ids import new_id
from .models import Connection, Conversation, Event, Message, OutboxJob, Project, ProviderEvent
from .net import is_public_url
from .otp import extract_otp_code
from .providers.base import (
    Capability,
    ChannelProvider,
    OutboundMessage,
    ProvisionRequest,
    SendResult,
)
from .serialize import connection_out, message_out

log = logging.getLogger("comm.jobs")

MAX_ATTEMPTS = 5


def enqueue(session: Session, job_type: str, payload: dict) -> None:
    session.add(OutboxJob(type=job_type, payload=payload, status="pending"))


def ingest_inbound(session_factory, provider_name: str, inbound: list) -> int:
    """Persist verified inbound messages (deduped) and queue normalization.

    Shared by the webhook route and the persistent listeners (Discord Gateway,
    telegram-user MTProto). Returns the number of newly-ingested events.
    """
    count = 0
    with session_factory() as session:
        # Prefetch existing event ids in one query instead of one SELECT per item.
        incoming_ids = {item.external_event_id for item in inbound}
        seen: set[str] = set(
            session.execute(
                select(ProviderEvent.external_event_id).where(
                    ProviderEvent.provider == provider_name,
                    ProviderEvent.external_event_id.in_(incoming_ids),
                )
            ).scalars()
        )
        for item in inbound:
            if item.external_event_id in seen:
                continue
            # Track within-batch too, so a repeated id in one batch is deduped
            # exactly as the prior autoflush-per-item query did.
            seen.add(item.external_event_id)
            # kind ("message" | "interaction" | "reaction") maps to the event type
            # so the same ingest/dispatch path serves messages, button taps, and
            # reactions. Defaults to "message" for every existing provider.
            kind = getattr(item, "kind", "message") or "message"
            provider_event = ProviderEvent(
                id=new_id("pev"),
                provider=provider_name,
                external_event_id=item.external_event_id,
                event_type=f"{kind}.received",
                payload=item.to_payload(),
            )
            session.add(provider_event)
            enqueue(session, "process_provider_event", {"provider_event_id": provider_event.id})
            count += 1
        session.commit()
    return count


def emit_event(session: Session, project_id: str, event_type: str, data: dict) -> Event:
    event = Event(id=new_id("evt"), project_id=project_id, type=event_type, payload=data)
    session.add(event)
    session.flush()
    # Server-side product analytics (best-effort, metadata only — see analytics.py).
    from .analytics import capture, safe_props

    capture(project_id, f"gateway.{event_type}", safe_props(event_type, data))
    # Every event is pollable via GET /v1/events AND pushed to the project's
    # webhook if one is configured — agents choose polling, webhooks, or both.
    # Read only the webhook columns rather than loading the full Project row.
    webhook = session.execute(
        select(Project.webhook_url, Project.webhook_secret).where(Project.id == project_id)
    ).first()
    if webhook is not None and webhook.webhook_url:
        enqueue(
            session,
            "deliver_event_webhook",
            {
                "project_id": project_id,
                "event": {
                    "id": event.id,
                    "type": event_type,
                    "occurred_at": event.created_at.isoformat(),
                    "data": data,
                },
            },
        )
    return event


def _emit_message(
    session: Session, connection: Connection, event_type: str, message: Message
) -> None:
    """Emit a message.* event with the standard envelope."""
    emit_event(
        session,
        connection.project_id,
        event_type,
        {
            "customer_id": connection.customer_id,
            "agent_id": connection.agent_id,
            "connection_id": connection.id,
            "message": message_out(message),
        },
    )


def _mark_sent(
    session: Session, connection: Connection, message: Message, result: SendResult
) -> None:
    """Record a provider send result on an outbound message and announce it."""
    message.provider_message_id = result.provider_message_id
    message.status = "sent"
    _emit_message(session, connection, "message.sent", message)


def run_pending_jobs(session_factory, providers: dict, max_jobs: int = 100) -> int:
    handled = 0
    while handled < max_jobs:
        with session_factory() as session:
            candidate_seq = session.execute(
                select(OutboxJob.seq)
                .where(OutboxJob.status == "pending")
                .order_by(OutboxJob.seq)
                .limit(1)
            ).scalar_one_or_none()
            if candidate_seq is None:
                break
            # Atomically claim it: the WHERE clause re-checks status="pending" at
            # UPDATE time, so if another worker claimed this same row between our
            # SELECT and here, exactly one of the two UPDATEs matches a row (this
            # is a plain conditional UPDATE, so it works identically on SQLite and
            # Postgres - no FOR UPDATE SKIP LOCKED needed).
            claimed = session.execute(
                update(OutboxJob)
                .where(OutboxJob.seq == candidate_seq, OutboxJob.status == "pending")
                .values(status="claimed")
            ).rowcount
            session.commit()
            if not claimed:
                continue  # lost the race for this job; look for the next one
            job = session.get(OutboxJob, candidate_seq)
            try:
                handler = _HANDLERS[job.type]
                handler(session, providers, job.payload)
                job.status = "done"
                job.error = None
            except Exception as exc:
                session.rollback()
                job = session.get(OutboxJob, candidate_seq)
                job.attempts += 1
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "pending" if job.attempts < MAX_ATTEMPTS else "failed"
                log.warning("job %s attempt %d failed: %s", job.type, job.attempts, exc)
                if job.status == "failed":
                    _on_permanent_failure(session, job)
            session.commit()
            handled += 1
    return handled

def _on_permanent_failure(session: Session, job: OutboxJob) -> None:
    if job.type == "provision_connection":
        connection = session.get(Connection, job.payload["connection_id"])
        if connection is not None and connection.status == "provisioning":
            connection.status = "failed"
            connection.error = job.error
            emit_event(
                session,
                connection.project_id,
                "connection.failed",
                {
                    "connection": connection_out(connection),
                    "channel": connection.channel,
                    "provider": connection.provider,
                },
            )


def _resolve(providers: dict, name: str) -> ChannelProvider:
    provider = providers.get(name)
    if provider is None:
        raise RuntimeError(f"no provider configured for {name!r}")
    return provider


def _live_credentials(session: Session, connection: Connection, provider) -> dict:
    """Return usable credentials, rotating an expired OAuth token first.

    Providers with rotating tokens (Slack) expose needs_refresh/refresh_
    credentials; when the access token is near expiry we refresh and persist the
    new (also-rotated) refresh token so the next call has a valid pair.
    """
    credentials = read_credentials(connection)
    if hasattr(provider, "needs_refresh") and provider.needs_refresh(credentials):
        refreshed = provider.refresh_credentials(credentials)
        credentials = {**credentials, **refreshed}
        write_credentials(connection, credentials)
        session.flush()
        log.info("refreshed rotating token for connection %s", connection.id)
    return credentials


def _provision_connection(session: Session, providers: dict, payload: dict) -> None:
    connection = session.get(Connection, payload["connection_id"])
    if connection is None or connection.status != "provisioning":
        return
    provider = _resolve(providers, connection.provider)
    result = provider.provision(
        ProvisionRequest(
            connection_id=connection.id,
            customer_id=connection.customer_id,
            agent_id=connection.agent_id,
            display_name=payload.get("display_name"),
            credentials=read_credentials(connection),
            domain=payload.get("domain"),
            username=payload.get("username"),
        )
    )
    connection.address = result.address
    connection.provider_resource_id = result.provider_resource_id
    connection.provider_pod_id = result.provider_pod_id
    # A resource (phone number, page id, ...) backs only ONE connection at a time:
    # inbound routes by (provider, resource_id), so a second claimant would make
    # that lookup ambiguous and crash. This is what makes the shared Twilio
    # sandbox number single-tenant. Fail the duplicate cleanly (permanent, so no
    # retry) instead of activating it — the connect call surfaces `error`.
    if result.provider_resource_id:
        clash = session.execute(
            select(Connection).where(
                Connection.provider == connection.provider,
                Connection.provider_resource_id == result.provider_resource_id,
                Connection.id != connection.id,
                Connection.status.in_(("provisioning", "active")),
            )
        ).scalars().first()
        if clash is not None:
            connection.status = "failed"
            connection.error = (
                f"{connection.channel} identity {result.provider_resource_id!r} is already "
                "connected; each connection needs its own number/resource (the Twilio "
                "sandbox number is shared and single-tenant)"
            )
            emit_event(
                session,
                connection.project_id,
                "connection.failed",
                {
                    "connection": connection_out(connection),
                    "channel": connection.channel,
                    "provider": connection.provider,
                },
            )
            return
    connection.status = "active"
    connection.error = None
    emit_event(
        session,
        connection.project_id,
        "connection.active",
        {
            "connection": connection_out(connection),
            "channel": connection.channel,
            "provider": connection.provider,
        },
    )


def _find_conversation(session: Session, connection: Connection, thread_id: str):
    return session.execute(
        select(Conversation).where(
            Conversation.connection_id == connection.id,
            Conversation.provider_thread_id == thread_id,
        )
    ).scalar_one_or_none()


def _source_message(session: Session, connection: Connection, provider_message_id: str | None):
    """The stored Message a button/reaction targets, matched by provider id."""
    if not provider_message_id:
        return None
    return session.execute(
        select(Message).where(
            Message.connection_id == connection.id,
            Message.provider_message_id == provider_message_id,
        )
    ).scalar_one_or_none()


def _process_interaction(session: Session, connection: Connection, data: dict) -> None:
    """A button tap round-tripping back to the agent as an interaction.received
    event (distinct from message.received so handlers subscribe to it separately)."""
    action = data.get("action") or {}
    source = _source_message(session, connection, action.get("source_message_id"))
    conversation = _find_conversation(session, connection, data.get("provider_thread_id"))
    emit_event(
        session,
        connection.project_id,
        "interaction.received",
        {
            "customer_id": connection.customer_id,
            "agent_id": connection.agent_id,
            "connection_id": connection.id,
            "conversation_id": conversation.id if conversation else None,
            "value": action.get("value"),
            "source_message": message_out(source) if source else None,
            "sender": {"address": data.get("sender_address"), "name": data.get("sender_name")},
        },
    )


def _process_reaction(session: Session, connection: Connection, data: dict) -> None:
    """An emoji reaction on one of our messages, surfaced as reaction.received."""
    reaction = data.get("reaction") or {}
    source = _source_message(session, connection, reaction.get("source_message_id"))
    emit_event(
        session,
        connection.project_id,
        "reaction.received",
        {
            "customer_id": connection.customer_id,
            "agent_id": connection.agent_id,
            "connection_id": connection.id,
            "emoji": reaction.get("emoji"),
            "action": reaction.get("action", "added"),
            "source_message": message_out(source) if source else None,
            "sender": {"address": data.get("sender_address"), "name": data.get("sender_name")},
        },
    )


def _process_provider_event(session: Session, providers: dict, payload: dict) -> None:
    provider_event = session.get(ProviderEvent, payload["provider_event_id"])
    if provider_event is None or provider_event.processed:
        return
    data = provider_event.payload
    # Route only to a LIVE connection: failed/disconnected connections keep their
    # resource_id, so without the status filter a superseded duplicate would make
    # this lookup ambiguous. The provision guard keeps at most one live connection
    # per (provider, resource_id), so this resolves to exactly one.
    connection = session.execute(
        select(Connection).where(
            Connection.provider == provider_event.provider,
            Connection.provider_resource_id == data["provider_inbox_id"],
            Connection.status.in_(("provisioning", "active")),
        )
    ).scalar_one_or_none()
    if connection is None:
        log.warning("no connection for provider inbox %s", data["provider_inbox_id"])
        provider_event.processed = True
        return

    # Button taps and reactions don't create messages; they emit their own event
    # against the message they target. Only "message" flows into the store below.
    kind = data.get("kind", "message")
    if kind == "interaction":
        _process_interaction(session, connection, data)
        provider_event.processed = True
        return
    if kind == "reaction":
        _process_reaction(session, connection, data)
        provider_event.processed = True
        return

    conversation = session.execute(
        select(Conversation).where(
            Conversation.connection_id == connection.id,
            Conversation.provider_thread_id == data["provider_thread_id"],
        )
    ).scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(
            id=new_id("conv"),
            project_id=connection.project_id,
            connection_id=connection.id,
            subject=data.get("subject"),
            provider_thread_id=data["provider_thread_id"],
        )
        session.add(conversation)

    duplicate = session.execute(
        select(Message).where(
            Message.connection_id == connection.id,
            Message.provider_message_id == data["provider_message_id"],
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        # An edit of a message we already have: update text and emit an edit
        # event instead of silently dropping it (Capability.EDIT_INBOUND).
        if data.get("edited") and duplicate.text != data.get("text"):
            duplicate.text = data.get("text")
            duplicate.edited = True
            _emit_message(session, connection, "message.edited", duplicate)
        provider_event.processed = True
        return

    message = Message(
        id=new_id("msg"),
        project_id=connection.project_id,
        conversation_id=conversation.id,
        connection_id=connection.id,
        channel=connection.channel,
        direction="inbound",
        status="received",
        chat_type=data.get("chat_type"),
        auto_generated=bool(data.get("auto_generated")),
        sender_address=data.get("sender_address"),
        sender_name=data.get("sender_name"),
        recipients=data.get("recipients") or [],
        subject=data.get("subject"),
        text=data.get("text"),
        html=data.get("html"),
        media=data.get("media") or [],
        provider_message_id=data["provider_message_id"],
    )
    session.add(message)
    session.flush()
    if message.auto_generated:
        # Auto-responders, bounces, no-reply senders: stored for the record but
        # no message.received event, so agents never reply and loops can't form.
        log.info("suppressing event for auto-generated message %s", message.id)
    else:
        _emit_message(session, connection, "message.received", message)
        # On a real-mobile line that grants OTP, surface any verification code as
        # a distinct message.otp event so an agent can consume it without parsing.
        if Capability.OTP in (connection.capabilities or []):
            code = extract_otp_code(message.text)
            if code is not None:
                emit_event(
                    session,
                    connection.project_id,
                    "message.otp",
                    {
                        "customer_id": connection.customer_id,
                        "agent_id": connection.agent_id,
                        "connection_id": connection.id,
                        "code": code,
                        "message": message_out(message),
                    },
                )
    provider_event.processed = True


def _send_reply(session: Session, providers: dict, payload: dict) -> None:
    message = session.get(Message, payload["message_id"])
    if message is None or message.status != "queued":
        return
    target = session.get(Message, message.in_reply_to_id) if message.in_reply_to_id else None
    if target is None:
        log.warning("send_reply for message %s has no reply target; skipping", message.id)
        return
    connection = session.get(Connection, message.connection_id)
    provider = _resolve(providers, connection.provider)
    recipients = tuple(
        r["address"] for r in (message.recipients or []) if r.get("address")
    )
    result = provider.reply(
        connection.provider_resource_id,
        target.provider_message_id,
        OutboundMessage(
            text=message.text,
            html=message.html,
            subject=message.subject,
            to=recipients,
            blocks=tuple(payload["blocks"]) if payload.get("blocks") else None,
            media=tuple(message.media) if message.media else None,
        ),
        credentials=_live_credentials(session, connection, provider),
    )
    _mark_sent(session, connection, message, result)


def _send_message(session: Session, providers: dict, payload: dict) -> None:
    message = session.get(Message, payload["message_id"])
    if message is None or message.status != "queued":
        return
    connection = session.get(Connection, message.connection_id)
    conversation = session.get(Conversation, message.conversation_id)
    provider = _resolve(providers, connection.provider)
    result = provider.send(
        connection.provider_resource_id,
        OutboundMessage(
            text=message.text,
            html=message.html,
            subject=message.subject,
            to=(conversation.provider_thread_id,),
            blocks=tuple(payload["blocks"]) if payload.get("blocks") else None,
            media=tuple(message.media) if message.media else None,
        ),
        credentials=_live_credentials(session, connection, provider),
    )
    _mark_sent(session, connection, message, result)


def _initiate(session: Session, providers: dict, payload: dict) -> None:
    connection = session.get(Connection, payload["connection_id"])
    if connection is None or connection.status != "active":
        return
    provider = _resolve(providers, connection.provider)
    result = provider.initiate(
        connection.provider_resource_id,
        payload["recipient"],
        OutboundMessage(
            text=payload.get("text"),
            blocks=tuple(payload["blocks"]) if payload.get("blocks") else None,
            media=tuple(payload["media"]) if payload.get("media") else None,
        ),
        credentials=_live_credentials(session, connection, provider),
    )
    conversation = session.execute(
        select(Conversation).where(
            Conversation.connection_id == connection.id,
            Conversation.provider_thread_id == result.provider_thread_id,
        )
    ).scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(
            id=new_id("conv"),
            project_id=connection.project_id,
            connection_id=connection.id,
            subject=None,
            provider_thread_id=result.provider_thread_id,
        )
        session.add(conversation)
        session.flush()
    message = Message(
        id=new_id("msg"),
        project_id=connection.project_id,
        conversation_id=conversation.id,
        connection_id=connection.id,
        channel=connection.channel,
        direction="outbound",
        status="sent",
        sender_address=connection.address,
        recipients=[{"address": payload["recipient"]}],
        text=payload["text"],
        media=payload.get("media") or [],
        provider_message_id=result.provider_message_id,
    )
    session.add(message)
    session.flush()
    _emit_message(session, connection, "message.sent", message)


def _backfill(session: Session, providers: dict, payload: dict) -> None:
    conversation = session.get(Conversation, payload["conversation_id"])
    if conversation is None:
        return
    connection = session.get(Connection, conversation.connection_id)
    provider = _resolve(providers, connection.provider)
    history = provider.backfill(
        connection.provider_resource_id,
        conversation.provider_thread_id,
        payload.get("limit", 50),
        credentials=_live_credentials(session, connection, provider),
    )
    for item in history:
        exists = session.execute(
            select(Message).where(
                Message.connection_id == connection.id,
                Message.provider_message_id == item.provider_message_id,
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        message = Message(
            id=new_id("msg"),
            project_id=connection.project_id,
            conversation_id=conversation.id,
            connection_id=connection.id,
            channel=connection.channel,
            direction="inbound",
            status="received",
            chat_type=item.chat_type,
            sender_address=item.sender_address,
            sender_name=item.sender_name,
            text=item.text,
            provider_message_id=item.provider_message_id,
        )
        session.add(message)
        session.flush()
        _emit_message(session, connection, "message.backfilled", message)


def _release_connection(session: Session, providers: dict, payload: dict) -> None:
    connection = session.get(Connection, payload["connection_id"])
    if connection is None or connection.status == "released":
        return
    provider = _resolve(providers, connection.provider)
    release = getattr(provider, "release", None)
    if release is not None and connection.provider_resource_id:
        release(connection.provider_resource_id, connection.provider_pod_id)
    connection.status = "released"
    emit_event(
        session,
        connection.project_id,
        "connection.released",
        {"connection": connection_out(connection)},
    )


def _deliver_event_webhook(session: Session, providers: dict, payload: dict) -> None:
    project = session.get(Project, payload["project_id"])
    if project is None or not project.webhook_url:
        return
    # SSRF guard at delivery time too (catches DNS-rebinding since set-time).
    if not is_public_url(project.webhook_url):
        log.warning("skipping webhook delivery to non-public url for project %s", project.id)
        return
    body = json.dumps(payload["event"]).encode()
    headers = {"content-type": "application/json"}
    if project.webhook_secret:
        headers["x-caspian-signature"] = "sha256=" + hmac.new(
            project.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
    resp = httpx.post(project.webhook_url, content=body, headers=headers, timeout=15)
    resp.raise_for_status()  # non-2xx -> job retries (up to MAX_ATTEMPTS)


_HANDLERS = {
    "provision_connection": _provision_connection,
    "deliver_event_webhook": _deliver_event_webhook,
    "process_provider_event": _process_provider_event,
    "send_reply": _send_reply,
    "send_message": _send_message,
    "initiate": _initiate,
    "backfill": _backfill,
    "release_connection": _release_connection,
}
