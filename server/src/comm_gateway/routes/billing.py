"""Billing API: pay-as-you-go with no dashboard, ever.

The agent (or its developer) does everything here over REST:
- GET  /v1/billing            balance, spend, limits, autopay state
- POST /v1/billing/topup      -> Stripe-hosted checkout URL (the only human page)
- PUT  /v1/billing/limits     monthly / per-channel caps
- PUT  /v1/billing/autopay    off-session replenishment (requires saved card + cap)
- POST /internal/billing/stripe/webhooks   signature-verified crediting
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_project, get_session
from ..billing import (
    MAX_TOPUP_CENTS,
    MIN_TOPUP_CENTS,
    _stripe,
    billing_summary,
    credit_topup,
    get_billing,
)
from ..models import Project

log = logging.getLogger("comm.billing")

router = APIRouter()


class TopupCreate(BaseModel):
    amount_cents: int = Field(ge=MIN_TOPUP_CENTS, le=MAX_TOPUP_CENTS)


class LimitsUpdate(BaseModel):
    monthly_cap_cents: int | None = Field(default=None, ge=0)
    channel_caps: dict[str, int] | None = None


class AutopayUpdate(BaseModel):
    enabled: bool = True
    threshold_cents: int | None = Field(default=None, ge=100)
    topup_cents: int | None = Field(default=None, ge=MIN_TOPUP_CENTS, le=MAX_TOPUP_CENTS)
    monthly_cap_cents: int | None = Field(default=None, ge=MIN_TOPUP_CENTS)


@router.get("/v1/billing")
def get_billing_state(
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    summary = billing_summary(session, request, project.id)
    session.commit()  # persist a first-touch account creation
    return summary


@router.post("/v1/billing/topup", status_code=201)
def create_topup(
    body: TopupCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Mint a Stripe-hosted checkout link. Whoever holds the link pays - the
    developer, or the agent itself if it can drive a browser and holds a card."""
    settings = request.app.state.settings
    account = get_billing(session, project.id)
    data = {
        "mode": "payment",
        "line_items[0][price_data][currency]": settings.billing_currency,
        "line_items[0][price_data][unit_amount]": body.amount_cents,
        "line_items[0][price_data][product_data][name]": "Caspian credit",
        "line_items[0][quantity]": 1,
        "success_url": settings.billing_success_url,
        "cancel_url": settings.billing_cancel_url,
        "client_reference_id": project.id,
        "metadata[project_id]": project.id,
        # Save the card so the agent can turn on autopay afterwards.
        "payment_intent_data[setup_future_usage]": "off_session",
        "payment_intent_data[metadata][project_id]": project.id,
    }
    if account.stripe_customer_id:
        data["customer"] = account.stripe_customer_id
    else:
        data["customer_creation"] = "always"
    checkout = _stripe(settings, "POST", "/checkout/sessions", data)
    session.commit()
    return {
        "checkout_url": checkout["url"],
        "session_id": checkout["id"],
        "amount_cents": body.amount_cents,
        "note": "Credit lands seconds after payment; poll GET /v1/billing or watch "
                "for the billing.credited event.",
    }


@router.put("/v1/billing/limits")
def update_limits(
    body: LimitsUpdate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    account = get_billing(session, project.id)
    if body.monthly_cap_cents is not None:
        account.monthly_cap_cents = body.monthly_cap_cents or None
    if body.channel_caps is not None:
        account.channel_caps = {k: int(v) for k, v in body.channel_caps.items()} or None
    if account.autopay_threshold_cents is not None and account.monthly_cap_cents is None:
        raise HTTPException(
            status_code=409,
            detail="Autopay is enabled, so a monthly_cap_cents is required "
                   "(disable autopay first to remove the cap).",
        )
    session.commit()
    return billing_summary(session, request, project.id)


@router.put("/v1/billing/autopay")
def update_autopay(
    body: AutopayUpdate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    account = get_billing(session, project.id)
    if not body.enabled:
        account.autopay_threshold_cents = None
        account.autopay_topup_cents = None
        session.commit()
        return billing_summary(session, request, project.id)

    if body.threshold_cents is None or body.topup_cents is None:
        raise HTTPException(
            status_code=422, detail="Enabling autopay requires threshold_cents and topup_cents"
        )
    cap = body.monthly_cap_cents or account.monthly_cap_cents
    if cap is None:
        raise HTTPException(
            status_code=422,
            detail="Autopay requires a monthly_cap_cents - an uncapped auto-replenishing "
                   "budget is not allowed.",
        )
    if not (account.stripe_customer_id and account.stripe_payment_method):
        raise HTTPException(
            status_code=409,
            detail="No saved payment method yet. Complete one checkout "
                   "(POST /v1/billing/topup) first; the card is saved automatically.",
        )
    account.autopay_threshold_cents = body.threshold_cents
    account.autopay_topup_cents = body.topup_cents
    account.monthly_cap_cents = cap
    session.commit()
    return billing_summary(session, request, project.id)


# How far a webhook's signed timestamp may drift from now before we reject it.
# Matches Stripe's own default and stops a captured payload being replayed later.
STRIPE_SIGNATURE_TOLERANCE = 300  # seconds


def _verify_stripe_signature(
    payload: bytes, header: str, secret: str, tolerance: int = STRIPE_SIGNATURE_TOLERANCE
) -> bool:
    """Stripe-Signature: t=<ts>,v1=<hmac_sha256(f"{t}.{payload}", secret)>.

    Verifies the HMAC (timing-safe) AND that the signed timestamp is recent, so
    a valid-but-old event can't be replayed to double-credit an account."""
    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    timestamp, signature = parts.get("t", ""), parts.get("v1", "")
    if not (timestamp and signature):
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > tolerance:
        log.warning("stripe webhook timestamp outside tolerance (%.0fs)", age)
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/internal/billing/stripe/webhooks", status_code=204)
async def stripe_webhook(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    payload = await request.body()
    # Fail CLOSED: a billing webhook that credits real money must never process
    # an unverified event. If the signing secret isn't configured, refuse.
    if not settings.stripe_webhook_secret:
        log.error("stripe_webhook_secret is not set; refusing to process billing webhook")
        raise HTTPException(status_code=503, detail="Billing webhook is not configured")
    header = request.headers.get("stripe-signature", "")
    if not _verify_stripe_signature(payload, header, settings.stripe_webhook_secret):
        raise HTTPException(status_code=403, detail="Bad signature")
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if kind == "checkout.session.completed":
        project_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get(
            "project_id"
        )
        if not project_id:
            return
        credited = credit_topup(
            session, project_id, "stripe", obj.get("id", ""), int(obj.get("amount_total") or 0)
        )
        account = get_billing(session, project_id)
        if obj.get("customer"):
            account.stripe_customer_id = obj["customer"]
        # Save the card for autopay: read the payment method off the intent.
        intent_id = obj.get("payment_intent")
        if credited and intent_id and settings.stripe_secret_key:
            try:
                intent = _stripe(settings, "GET", f"/payment_intents/{intent_id}")
                if intent.get("payment_method"):
                    account.stripe_payment_method = intent["payment_method"]
            except HTTPException:
                log.warning("could not fetch intent %s to save payment method", intent_id)
        session.commit()

    elif kind == "payment_intent.succeeded":
        metadata = obj.get("metadata") or {}
        if metadata.get("caspian") == "autopay" and metadata.get("project_id"):
            credit_topup(
                session, metadata["project_id"], "stripe-autopay",
                obj.get("id", ""), int(obj.get("amount") or 0),
            )
