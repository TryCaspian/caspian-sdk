from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# Free launch credit granted once on first sign-in, in cents. Set to 0: new
# signups start with no free credit and top up before using paid channels.
FREE_CREDIT_CENTS = 0


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    webhook_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DashboardAccount(Base):
    """Links a developer's dashboard identity (Supabase/Google email) to their
    Caspian project, and stores their API key (encrypted) so the dashboard can
    show them a ready-to-paste setup prompt. One developer = one project."""

    __tablename__ = "dashboard_accounts"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    api_key_enc: Mapped[dict] = mapped_column(JSON)  # encrypted {"api_key": ...}
    # Launch-credit grant, in cents (see FREE_CREDIT_CENTS; currently 0).
    credit_cents: Mapped[int] = mapped_column(Integer, default=FREE_CREDIT_CENTS)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        Index("ix_connections_provider_resource", "provider", "provider_resource_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    channel: Mapped[str] = mapped_column(String(20), default="email")
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="provisioning")
    address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_resource_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    provider_pod_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    provider_credentials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending_dns")
    dns_records: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("connection_id", "provider_thread_id", name="uq_conversation_thread"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), index=True)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    provider_thread_id: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_provider_message", "connection_id", "provider_message_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    direction: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))
    sender_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # File attachments carried by this message (inbound or outbound). Each item is
    # a dict: {"url"|"data", "mime_type", "name", "size"}.
    media: Mapped[list] = mapped_column(JSON, default=list)
    provider_message_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    chat_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    in_reply_to_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_provider_event"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_event_id: Mapped[str] = mapped_column(String(320))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboxJob(Base):
    __tablename__ = "outbox_jobs"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_project_seq", "project_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeviceAuth(Base):
    """OAuth 2.0 device-authorization flow (RFC 8628) for agent/CLI sign-in.

    The agent starts a flow (no auth), shows the developer a verification link,
    and polls until the developer signs in with Google in the browser. On
    approval we bind the flow to their account and hand the agent the API key.
    """

    __tablename__ = "device_auths"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    device_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    api_key_enc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # If the agent already built with an anonymous (no-signup) key, it passes that
    # key on start; on approval we bind THAT project to the account so the
    # developer keeps everything they built. Encrypted key, resolved project id.
    link_project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    link_api_key_enc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class BillingAccount(Base):
    """Per-project pay-as-you-go billing state. Keyed by project so anonymous
    (no-signup) projects can pay too — no dashboard account required. The
    balance is credit_cents (all grants + top-ups) minus the provider cost
    accrued from Message rows (see billing.spent_cents); nothing is decremented
    at send time, so webhook replays and recounts stay consistent."""

    __tablename__ = "billing_accounts"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    credit_cents: Mapped[int] = mapped_column(Integer, default=0)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stripe_payment_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Autopay: when balance < threshold, charge topup_cents off-session. Only
    # allowed with a saved payment method AND a monthly cap.
    autopay_threshold_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    autopay_topup_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    autopay_last_attempt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Spend limits, set by the agent over the API.
    monthly_cap_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_caps: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BillingTopup(Base):
    """Ledger of processed credits. external_id is unique so a replayed Stripe
    webhook (or a double-submitted x402 settlement) can never credit twice."""

    __tablename__ = "billing_topups"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source: Mapped[str] = mapped_column(String(20))  # stripe | stripe-autopay | grant | x402
    external_id: Mapped[str] = mapped_column(String(120), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InternalProject(Base):
    """Team marker: this project is one of ours (a developer's test agent), not
    a customer. Set from the internal metrics dashboard so real usage can be
    told apart from our own testing. Nothing in the product reads it."""

    __tablename__ = "internal_projects"

    project_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    marked_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
