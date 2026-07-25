"""Pay-as-you-go billing: credit ledger, Stripe rail, limits, events.

Design rules (PRD: the developer NEVER visits our dashboard):
- Everything is an API call or an event. Stripe's hosted checkout is the only
  human-facing page, and Stripe owns it.
- The balance is derived: credit_cents (grants + top-ups) minus provider cost
  accrued from Message rows. Nothing decrements at send time, so webhook
  replays, retries, and recounts can never drift the ledger.
- A 402 always tells the caller how to fix it (payment_options), so the
  failure is self-healing for an agent reading it.
- Any auto-replenishing rail REQUIRES a monthly cap: an agent in a retry loop
  with a funded card is the failure mode we never want to explain to a
  customer.
"""

import logging
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ids import new_id
from .models import BillingAccount, BillingTopup, DashboardAccount, Event, Message, utcnow
from .routes.usage import COST, project_has_account

log = logging.getLogger("comm.billing")

# Channels whose sends/receives cost Caspian money (derived from the cost
# table, email excluded: its per-message cost is covered by the free grant and
# email is the zero-friction on-ramp).
PAID_CHANNELS = sorted({channel for (channel, _) in COST if channel != "email"})

AUTOPAY_COOLDOWN = timedelta(minutes=10)
MIN_TOPUP_CENTS = 100
MAX_TOPUP_CENTS = 500_000


def _stripe(
    settings, method: str, path: str, data: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Raw Stripe REST call (form-encoded). Kept as one seam so tests stub it.

    Pass ``idempotency_key`` on any request that creates a charge so a retry
    (network blip, double-fire) can never bill the card twice - Stripe returns
    the original result for a repeated key (24h window)."""
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Billing is not configured on this gateway")
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    response = httpx.request(
        method,
        f"{settings.stripe_base_url}/v1{path}",
        data=data,
        headers=headers,
        auth=(settings.stripe_secret_key, ""),
        timeout=20.0,
    )
    body = response.json()
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message", "Stripe request failed")
        log.warning("stripe %s %s -> %s: %s", method, path, response.status_code, message)
        raise HTTPException(status_code=502, detail=f"Payment provider error: {message}")
    return body


def get_billing(session: Session, project_id: str) -> BillingAccount:
    """Get-or-create the project's billing account.

    First touch migrates the legacy dashboard-account grant (credit_cents on
    DashboardAccount) into the project-keyed account, so existing signed-in
    developers keep their free credit without any action.
    """
    account = session.get(BillingAccount, project_id)
    if account is not None:
        return account
    legacy = session.execute(
        select(DashboardAccount).where(DashboardAccount.project_id == project_id)
    ).scalar_one_or_none()
    account = BillingAccount(
        project_id=project_id,
        credit_cents=legacy.credit_cents if legacy is not None else 0,
    )
    session.add(account)
    session.flush()
    if legacy is not None and legacy.credit_cents:
        session.add(BillingTopup(
            id=new_id("top"), project_id=project_id, source="grant",
            external_id=f"legacy:{project_id}", amount_cents=legacy.credit_cents,
        ))
    return account


def spent_cents(session: Session, project_id: str, since: datetime | None = None) -> int:
    """Provider cost accrued from Message rows, in cents (ceil-free float sum,
    rounded once at the end — matches the dashboard's cost_total)."""
    query = (
        select(Message.channel, Message.direction, func.count())
        .where(Message.project_id == project_id)
        .group_by(Message.channel, Message.direction)
    )
    if since is not None:
        query = query.where(Message.created_at >= since)
    total = 0.0
    for channel, direction, n in session.execute(query):
        total += COST.get((channel, direction), 0.0) * n * 100
    return int(round(total))


def month_start() -> datetime:
    now = utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def balance_cents(session: Session, account: BillingAccount) -> int:
    return account.credit_cents - spent_cents(session, account.project_id)


def login_options(base_url: str) -> list[dict]:
    """Machine-actionable way for an agent to get the developer signed in once.
    Paid channels require a real account (identity) behind the spend; this is the
    one-time step that opens it. After it, the agent tops up and connects on its
    own - no further human sign-in."""
    return [
        {
            "type": "device_login",
            "start": {"method": "POST", "url": f"{base_url}/v1/auth/device/start",
                      "body": {"api_key": "<this project's API key>"}},
            "poll": {"method": "POST", "url": f"{base_url}/v1/auth/device/token",
                     "body": {"device_code": "<device_code from start>"}},
            "note": "POST start with your API key (so THIS project binds to the "
                    "developer's account), show them verification_uri_complete, then "
                    "poll token until status=approved. Then add credit and retry.",
        },
    ]


def payment_options(dashboard_url: str) -> list[dict]:
    """Machine-actionable way out of a 402: the developer adds credit in the
    dashboard. We deliberately do NOT hand back a raw Stripe checkout link here -
    developers add credit from the dashboard billing page."""
    return [
        {
            "type": "dashboard",
            "url": dashboard_url,
            "note": "Add credit in the Caspian dashboard billing page. Show the "
                    "developer this link; credit lands within seconds of payment "
                    "(watch GET /v1/billing or the billing.credited event).",
        },
    ]


def billing_summary(session: Session, request, project_id: str) -> dict:
    account = get_billing(session, project_id)
    spent = spent_cents(session, project_id)
    spent_month = spent_cents(session, project_id, since=month_start())
    return {
        "credit_cents": account.credit_cents,
        "spent_cents": spent,
        "spent_this_month_cents": spent_month,
        "balance_cents": account.credit_cents - spent,
        "paid_channels": PAID_CHANNELS,
        "limits": {
            "monthly_cap_cents": account.monthly_cap_cents,
            "channel_caps": account.channel_caps or {},
        },
        "autopay": {
            "enabled": account.autopay_threshold_cents is not None,
            "threshold_cents": account.autopay_threshold_cents,
            "topup_cents": account.autopay_topup_cents,
            "payment_method_saved": account.stripe_payment_method is not None,
        },
    }


def credit_topup(
    session: Session, project_id: str, source: str, external_id: str, amount_cents: int
) -> bool:
    """Idempotently credit a processed payment. Returns False on a replay."""
    account = get_billing(session, project_id)
    session.add(BillingTopup(
        id=new_id("top"), project_id=project_id, source=source,
        external_id=external_id, amount_cents=amount_cents,
    ))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        log.info("duplicate topup %s ignored (replay)", external_id)
        return False
    account.credit_cents += amount_cents
    from .jobs import emit_event

    emit_event(session, project_id, "billing.credited", {
        "amount_cents": amount_cents, "source": source,
        "balance_cents": balance_cents(session, account),
    })
    session.commit()
    return True


def _recent_event(session: Session, project_id: str, event_type: str, within: timedelta) -> bool:
    row = session.execute(
        select(Event.created_at)
        .where(Event.project_id == project_id, Event.type == event_type)
        .order_by(Event.seq.desc())
        .limit(1)
    ).first()
    return row is not None and row[0] > utcnow() - within


def ensure_credit(request, session: Session, project, channel: str) -> None:
    """Gate a paid-channel action on balance and limits; 402/429 with a
    machine-actionable body when blocked. Free channels pass untouched."""
    if channel not in PAID_CHANNELS:
        return
    settings = request.app.state.settings
    base = settings.public_base_url or str(request.base_url).rstrip("/")

    # Paid channels require a one-time developer sign-in (a real account/identity)
    # BEFORE any spend, so every paying project is tied to a person we can bill,
    # support, and recover. Free channels never hit this. Skipped when sign-in
    # isn't configured (no Supabase) - then we fall back to credit-only gating.
    if settings.supabase_url and settings.supabase_anon_key and not project_has_account(
        session, project.id
    ):
        from .analytics import capture

        capture(project.id, "gateway.paywall_hit",
                {"reason": "account_required", "channel": channel})
        raise HTTPException(status_code=401, detail={
            "reason": "account_required",
            "message": (
                f"The {channel} channel is a paid channel. A developer must sign in "
                "to Caspian once (with Google) to open a billing account for this "
                "project; after that the agent adds credit and connects on its own. "
                "Free channels (email, Telegram, Discord, Slack) need no sign-in."
            ),
            "login_options": login_options(base),
        })

    account = get_billing(session, project.id)
    from .jobs import emit_event

    balance = balance_cents(session, account)
    if balance <= 0:
        from .analytics import capture

        capture(project.id, "gateway.paywall_hit",
                {"reason": "insufficient_credit", "channel": channel, "balance_cents": balance})
        raise HTTPException(status_code=402, detail={
            "reason": "insufficient_credit",
            "message": (
                f"The {channel} channel runs on Caspian's paid network and this "
                f"project's balance is {balance} cents. Add credit in the Caspian "
                f"dashboard to continue: {settings.billing_dashboard_url}"
            ),
            "balance_cents": balance,
            "payment_options": payment_options(settings.billing_dashboard_url),
        })

    spent_month = spent_cents(session, project.id, since=month_start())
    cap = account.monthly_cap_cents
    if cap is not None and spent_month >= cap:
        if not _recent_event(session, project.id, "billing.limit_reached", timedelta(hours=24)):
            emit_event(session, project.id, "billing.limit_reached", {
                "scope": "monthly", "cap_cents": cap, "spent_this_month_cents": spent_month,
            })
            session.commit()
        raise HTTPException(status_code=429, detail={
            "reason": "monthly_cap_reached",
            "message": "This project's monthly spend cap is reached. Raise it via "
                       "PUT /v1/billing/limits, or wait for the new month.",
            "cap_cents": cap, "spent_this_month_cents": spent_month,
        })

    caps = account.channel_caps or {}
    channel_cap = caps.get(channel)
    if channel_cap is not None:
        channel_spent = _channel_month_cents(session, project.id, channel)
        if channel_spent >= channel_cap:
            raise HTTPException(status_code=429, detail={
                "reason": "channel_cap_reached", "channel": channel,
                "cap_cents": channel_cap, "spent_this_month_cents": channel_spent,
            })

    if balance < settings.billing_low_balance_cents:
        if not _recent_event(session, project.id, "billing.low_balance", timedelta(hours=24)):
            emit_event(session, project.id, "billing.low_balance", {
                "balance_cents": balance,
                "payment_options": payment_options(settings.billing_dashboard_url),
            })
            session.commit()
        maybe_autopay(session, settings, account, balance)


def _channel_month_cents(session: Session, project_id: str, channel: str) -> int:
    query = (
        select(Message.direction, func.count())
        .where(
            Message.project_id == project_id,
            Message.channel == channel,
            Message.created_at >= month_start(),
        )
        .group_by(Message.direction)
    )
    total = 0.0
    for direction, n in session.execute(query):
        total += COST.get((channel, direction), 0.0) * n * 100
    return int(round(total))


def maybe_autopay(session: Session, settings, account: BillingAccount, balance: int) -> None:
    """Off-session top-up when the agent enabled autopay. Credits land via the
    webhook (payment_intent.succeeded), never inline - one crediting path.

    Concurrency-safe: two requests can cross the low-balance threshold at once,
    so the whole claim (cooldown check -> stamp autopay_last_attempt) runs under
    a row lock, and the Stripe charge carries an idempotency key derived from
    that stamp. Either guard alone would prevent a double charge; both together
    are belt and suspenders on real money."""
    if account.autopay_threshold_cents is None or balance >= account.autopay_threshold_cents:
        return
    if not (account.stripe_customer_id and account.stripe_payment_method):
        return
    cap = account.monthly_cap_cents
    topup = account.autopay_topup_cents or 0
    if cap is None:
        return  # enforced at configuration time too; belt and suspenders

    # Serialize the claim: lock the account row so a concurrent request can't
    # also pass the cooldown check before we stamp the attempt. On SQLite (tests)
    # FOR UPDATE is a no-op, which is fine - there is no real concurrency there.
    locked = session.get(BillingAccount, account.project_id, with_for_update=True)
    if locked is None:
        return
    now = utcnow()
    if locked.autopay_last_attempt and now - locked.autopay_last_attempt < AUTOPAY_COOLDOWN:
        session.commit()  # release the lock; another request already claimed it
        return
    credited_month = session.execute(
        select(func.coalesce(func.sum(BillingTopup.amount_cents), 0)).where(
            BillingTopup.project_id == locked.project_id,
            BillingTopup.created_at >= month_start(),
            BillingTopup.source == "stripe-autopay",
        )
    ).scalar_one()
    if credited_month + topup > cap:
        session.commit()  # release the lock
        return
    locked.autopay_last_attempt = now
    session.commit()  # persist the claim + release the lock before we charge
    # Idempotency key is bound to this exact claim (project + stamp), so a retry
    # of the same attempt reuses Stripe's original charge instead of a new one.
    idem = f"autopay:{locked.project_id}:{int(now.timestamp())}"
    try:
        _stripe(settings, "POST", "/payment_intents", {
            "amount": topup,
            "currency": settings.billing_currency,
            "customer": locked.stripe_customer_id,
            "payment_method": locked.stripe_payment_method,
            "off_session": "true",
            "confirm": "true",
            "metadata[project_id]": locked.project_id,
            "metadata[caspian]": "autopay",
        }, idempotency_key=idem)
    except HTTPException as exc:
        log.warning("autopay attempt failed for %s: %s", locked.project_id, exc.detail)
