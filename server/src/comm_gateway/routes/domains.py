"""Custom email domains.

Developers can serve agents from their own domain instead of the platform
default. Flow: add the domain (a dedicated subdomain, so their existing mail
is untouched) -> we hand back DNS records (and a zone file for bulk import)
-> they add them at their registrar -> we verify on read and activate
inbound routing.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_project, get_session
from ..ids import new_id
from ..jobs import emit_event
from ..models import Domain, Project
from ..schemas import DomainCreate, DomainOut

router = APIRouter(prefix="/v1")

_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _email_provider(request: Request):
    for provider in request.app.state.providers.values():
        if provider.channel == "email":
            return provider
    raise HTTPException(status_code=400, detail="No email provider configured")


def _domain_out(d: Domain) -> dict:
    return {
        "id": d.id,
        "domain": d.domain,
        "status": d.status,
        "dns_records": d.dns_records or [],
        "created_at": d.created_at.isoformat(),
    }


def _check_and_activate(request: Request, session: Session, domain: Domain) -> None:
    """Verify pending domains on read; activate inbound routing on success."""
    if domain.status != "pending_dns":
        return
    provider = _email_provider(request)
    if not hasattr(provider, "check_domain"):
        return
    if provider.check_domain(domain.domain):
        provider.enable_inbound(domain.domain)
        domain.status = "active"
        emit_event(
            session,
            domain.project_id,
            "domain.verified",
            {"domain": {"id": domain.id, "domain": domain.domain, "status": "active"}},
        )
        session.commit()


@router.post("/domains", response_model=DomainOut, status_code=201)
def add_domain(
    body: DomainCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    name = body.domain.strip().lower().rstrip(".")
    if not _DOMAIN_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="Not a valid domain name")
    if name.count(".") < 2:
        raise HTTPException(
            status_code=422,
            detail=(
                "Use a dedicated subdomain (e.g. agents.example.com), not the root domain - "
                "pointing the root's MX at us would take over all existing mail for it"
            ),
        )
    existing = session.execute(select(Domain).where(Domain.domain == name)).scalar_one_or_none()
    if existing is not None:
        if existing.project_id == project.id:
            return _domain_out(existing)
        raise HTTPException(status_code=409, detail="Domain is already claimed")

    provider = _email_provider(request)
    if not hasattr(provider, "create_domain"):
        raise HTTPException(status_code=422, detail="Email provider does not support domains")
    records = provider.create_domain(name)
    domain = Domain(
        id=new_id("dom"),
        project_id=project.id,
        domain=name,
        status="pending_dns",
        dns_records=records,
    )
    session.add(domain)
    session.commit()
    return _domain_out(domain)


@router.get("/domains", response_model=list[DomainOut])
def list_domains(
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    rows = (
        session.execute(
            select(Domain).where(Domain.project_id == project.id).order_by(Domain.created_at)
        )
        .scalars()
        .all()
    )
    for row in rows:
        _check_and_activate(request, session, row)
    return [_domain_out(d) for d in rows]


@router.get("/domains/{domain_id}", response_model=DomainOut)
def get_domain(
    domain_id: str,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    domain = session.get(Domain, domain_id)
    if domain is None or domain.project_id != project.id:
        raise HTTPException(status_code=404, detail="Domain not found")
    _check_and_activate(request, session, domain)
    return _domain_out(domain)


@router.get("/domains/{domain_id}/zone-file", response_class=PlainTextResponse)
def domain_zone_file(
    domain_id: str,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """BIND-format records for bulk import at the registrar."""
    domain = session.get(Domain, domain_id)
    if domain is None or domain.project_id != project.id:
        raise HTTPException(status_code=404, detail="Domain not found")
    lines = []
    for record in domain.dns_records or []:
        name = record["name"].rstrip(".") + "."
        if record["type"] == "MX":
            lines.append(f"{name} 3600 IN MX {record.get('priority', 10)} {record['value']}.")
        else:
            lines.append(f"{name} 3600 IN {record['type']} {record['value']}.")
    return "\n".join(lines) + "\n"
