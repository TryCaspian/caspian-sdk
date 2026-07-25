"""Developer dashboard: onboarding + usage.

The dashboard signs the developer in with Supabase (Google) and calls
``GET /v1/usage`` with the Supabase access token. The gateway:

1. validates the token against the Supabase project and reads the email,
2. gets-or-creates that developer's Caspian project + API key (one Google login
   maps to one project — all their bots live under it),
3. returns their real-cost analytics plus the API key + base URL so the dashboard
   can show a ready-to-paste setup prompt.

The dashboard never touches the database — the backend owns the data and the
cost logic (computed here in Python).
"""

import json
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_session, hash_key
from ..crypto import _decrypt, _encrypt
from ..ids import new_id
from ..models import (
    Agent,
    ApiKey,
    Connection,
    Customer,
    DashboardAccount,
    Message,
    Project,
)

log = logging.getLogger("comm.usage")

router = APIRouter(prefix="/v1")

# What Caspian actually pays the underlying providers, per message, by
# (channel, direction) — no markup. Bring-your-own channels (the developer's own
# Twilio/Telnyx number, Telegram, Discord, Slack) cost us nothing → not listed.
COST: dict[tuple[str, str], float] = {
    ("x", "outbound"): 0.015,
    ("x", "inbound"): 0.005,
    ("email", "outbound"): 0.0001,
    ("email", "inbound"): 0.00005,
    ("imessage", "outbound"): 0.01,
    ("whatsapp", "outbound"): 0.005,
    ("whatsapp", "inbound"): 0.005,
}


def _email_from_session(request: Request, token: str) -> str:
    """Validate the Supabase access token and return the signed-in email."""
    settings = request.app.state.settings
    if not (settings.supabase_url and settings.supabase_anon_key):
        raise HTTPException(status_code=503, detail="Dashboard auth is not configured")
    try:
        r = httpx.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": settings.supabase_anon_key},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not verify session") from exc
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    email = (r.json() or {}).get("email", "")
    if not email:
        raise HTTPException(status_code=401, detail="Session has no email")
    return email


def _ensure_default_scope(session: Session, project_id: str) -> None:
    if session.execute(
        select(Customer).where(Customer.project_id == project_id, Customer.name == "default")
    ).scalar_one_or_none() is None:
        session.add(Customer(id=new_id("cus"), project_id=project_id, name="default"))
    if session.execute(
        select(Agent).where(Agent.project_id == project_id, Agent.name == "default")
    ).scalar_one_or_none() is None:
        session.add(Agent(id=new_id("agt"), project_id=project_id, name="default"))


def project_has_account(session: Session, project_id: str) -> bool:
    """True if a signed-in dashboard account owns this project (i.e. NOT an
    anonymous no-signup project). Paid, Caspian-network channels require this."""
    return session.execute(
        select(DashboardAccount.email).where(DashboardAccount.project_id == project_id)
    ).first() is not None


def get_or_create_account(
    session: Session,
    email: str,
    settings,
    link_project_id: str | None = None,
    link_api_key: str | None = None,
) -> tuple[str, str]:
    """(project_id, api_key) for this developer, provisioned on first sign-in.

    If the developer already built with an anonymous no-signup key (link_project_id
    + link_api_key) and that project isn't owned yet, we bind THAT project to the
    account and keep its existing key — so nothing they built is lost. Otherwise a
    fresh project + key is created. A COMM_DASHBOARD_LINKS seed still maps a demo
    email to an existing project.
    """
    account = session.get(DashboardAccount, email)
    if account is not None:
        return account.project_id, _decrypt(account.api_key_enc)["api_key"]

    # Carry over the anonymous project the agent already built with, if unowned.
    if link_project_id and link_api_key:
        proj = session.get(Project, link_project_id)
        owned = session.execute(
            select(DashboardAccount.email).where(DashboardAccount.project_id == link_project_id)
        ).first()
        if proj is not None and owned is None:
            _ensure_default_scope(session, link_project_id)
            session.add(DashboardAccount(
                email=email, project_id=link_project_id,
                api_key_enc=_encrypt({"api_key": link_api_key}),
            ))
            session.commit()
            return link_project_id, link_api_key

    seeded: dict = {}
    if settings.dashboard_links.strip():
        try:
            seeded = json.loads(settings.dashboard_links)
        except ValueError:
            seeded = {}
    project_id = seeded.get(email)
    if project_id is None:
        project = Project(id=new_id("proj"), name=email)
        session.add(project)
        session.flush()  # insert the project before its FK children (Postgres enforces FKs)
        project_id = project.id

    api_key = f"comm_{secrets.token_hex(24)}"
    session.add(ApiKey(id=new_id("key"), project_id=project_id, key_hash=hash_key(api_key)))
    _ensure_default_scope(session, project_id)
    session.add(
        DashboardAccount(email=email, project_id=project_id,
                         api_key_enc=_encrypt({"api_key": api_key}))
    )
    session.commit()
    return project_id, api_key


def compute_usage(session: Session, project_id: str) -> dict:
    """Real-cost analytics for a project, computed in the backend."""
    platforms = sorted({
        c for c in session.execute(
            select(Connection.channel).where(
                Connection.project_id == project_id, Connection.status == "active"
            )
        ).scalars()
    })
    bots = session.execute(
        select(func.count()).select_from(Connection).where(
            Connection.project_id == project_id, Connection.status == "active"
        )
    ).scalar_one()
    people = session.execute(
        select(func.count(func.distinct(Message.sender_address))).where(
            Message.project_id == project_id, Message.direction == "inbound"
        )
    ).scalar_one()
    conversations = session.execute(
        select(func.count(func.distinct(Message.conversation_id))).where(
            Message.project_id == project_id
        )
    ).scalar_one()

    rows = session.execute(
        select(
            Message.channel, Message.direction, func.count().label("n"),
            func.count(func.distinct(Message.sender_address)).label("ppl"),
        )
        .where(Message.project_id == project_id)
        .group_by(Message.channel, Message.direction)
    ).all()

    by: dict[str, dict] = {}
    m_in = m_out = 0
    cost_total = 0.0
    for channel, direction, n, ppl in rows:
        c = by.setdefault(
            channel, {"channel": channel, "received": 0, "sent": 0, "people": 0, "cost": 0.0}
        )
        cost = COST.get((channel, direction), 0.0) * n
        c["cost"] = round(c["cost"] + cost, 6)
        cost_total += cost
        if direction == "inbound":
            c["received"], c["people"] = n, ppl
            m_in += n
        else:
            c["sent"] = n
            m_out += n

    by_channel = sorted(by.values(), key=lambda x: -(x["received"] + x["sent"]))
    return {
        "bots": bots,
        "platforms": platforms,
        "people": people,
        "conversations": conversations,
        "messages_total": m_in + m_out,
        "messages_in": m_in,
        "messages_out": m_out,
        "cost_total": round(cost_total, 4),
        "by_channel": by_channel,
    }


@router.get("/usage")
def usage(request: Request, session: Session = Depends(get_session)):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    settings = request.app.state.settings
    email = _email_from_session(request, token)
    project_id, api_key = get_or_create_account(session, email, settings)
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    account = session.get(DashboardAccount, email)
    usage_data = compute_usage(session, project_id)
    # credit_cents is the granted balance (static). Accrued provider cost is
    # subtracted from it so "remaining" always matches the total cost shown.
    granted = (account.credit_cents if account else 0) / 100
    spent = usage_data["cost_total"]
    return {
        "linked": True,
        "email": email,
        "project": project_id,
        "api_key": api_key,
        "base_url": base,
        "credit_granted": round(granted, 4),
        "credit_spent": round(spent, 4),
        "credit_remaining": round(max(0.0, granted - spent), 4),
        **usage_data,
    }
