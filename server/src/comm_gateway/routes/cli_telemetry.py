"""CLI product telemetry — thin proxy into PostHog.

The CLI POSTs allowlisted events here so we don't ship a PostHog write key in
the package. Auth is optional: with a Bearer key we attach ``project_id``; without
one (login-first ``caspian init``) only the allowlisted CLI events are accepted.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import capture, identify
from ..auth import get_session, hash_key
from ..models import ApiKey

router = APIRouter()

# Events the unauthenticated CLI may emit (pre-login init / anonymous machine).
ALLOWED_EVENTS = frozenset({
    "cli.session_started",
    "cli.session_ended",
    "cli.command_started",
    "cli.command_succeeded",
    "cli.command_failed",
    "cli.init_started",
    "cli.login_url_shown",
    "cli.login_approved",
    "cli.login_failed",
    "cli.connect_started",
    "cli.connect_authorize_shown",
    "cli.connect_succeeded",
    "cli.connect_failed",
})

# Scalar props we forward into PostHog (no tokens / message bodies).
_SAFE_PROP_KEYS = frozenset({
    "command", "cli_version", "cli_session_id", "duration_ms", "error_code",
    "sandbox", "channel", "os", "python_version", "argv_flags", "reason",
    "project_id", "email", "machine_id",
})


class TelemetryIn(BaseModel):
    event: str
    distinct_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


def _project_id_from_bearer(session: Session, authorization: str) -> str | None:
    if not authorization.startswith("Bearer "):
        return None
    key = authorization.removeprefix("Bearer ").strip()
    if not key:
        return None
    row = session.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_key(key))
    ).scalar_one_or_none()
    return row.project_id if row is not None else None


def _sanitize(properties: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in properties.items():
        if k not in _SAFE_PROP_KEYS:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = v[:20]  # flag names only
    return out


@router.post("/v1/cli/telemetry")
def cli_telemetry(
    body: TelemetryIn,
    session: Session = Depends(get_session),
    authorization: str = Header(default=""),
):
    """Ingest one CLI analytics event. Best-effort; always 204-style ok."""
    if body.event not in ALLOWED_EVENTS:
        return {"ok": False, "error": "event_not_allowed"}

    props = _sanitize(body.properties or {})
    project_id = _project_id_from_bearer(session, authorization)
    if project_id:
        props["project_id"] = project_id

    distinct = (body.distinct_id or "").strip()
    if not distinct:
        distinct = project_id or props.get("machine_id") or "anonymous"
        if distinct != "anonymous" and not str(distinct).startswith("anonymous:"):
            if not project_id and props.get("machine_id"):
                distinct = f"anonymous:{props['machine_id']}"

    # Prefer email as person id when the CLI learned it (post-login).
    email = props.get("email")
    if isinstance(email, str) and "@" in email:
        identify(email, {"email": email, "project_id": props.get("project_id")})
        distinct = email

    props["source"] = "cli"
    capture(str(distinct), body.event, props)
    return {"ok": True}
