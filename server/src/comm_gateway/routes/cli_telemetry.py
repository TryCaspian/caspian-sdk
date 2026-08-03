"""CLI product telemetry — thin proxy into PostHog.

The CLI POSTs allowlisted events here so we don't ship a PostHog write key in
the package. Auth is optional: with a Bearer key we attach ``project_id``; without
one (login-first ``caspian init``) only the allowlisted CLI events are accepted.
Unauthenticated callers cannot set ``project_id`` / ``email`` or trigger identify.
"""

import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
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
# ``project_id`` / ``email`` are only kept after a successful bearer lookup.
_SAFE_PROP_KEYS = frozenset({
    "command", "cli_version", "cli_session_id", "duration_ms", "error_code",
    "sandbox", "channel", "os", "python_version", "argv_flags", "reason",
    "project_id", "email", "machine_id",
})

# Unauthenticated ingest budget (per client IP).
_UNAUTH_LIMIT = 60
_UNAUTH_WINDOW_S = 60.0
_unauth_hits: dict[str, list[float]] = {}
_unauth_lock = threading.Lock()


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


def _throttle_unauth(client_ip: str) -> bool:
    """Return True if the unauthenticated request is within budget."""
    now = time.monotonic()
    cutoff = now - _UNAUTH_WINDOW_S
    with _unauth_lock:
        hits = _unauth_hits.setdefault(client_ip, [])
        hits[:] = [t for t in hits if t > cutoff]
        if len(hits) >= _UNAUTH_LIMIT:
            return False
        hits.append(now)
        if len(_unauth_hits) > 10_000:
            stale = [k for k, v in _unauth_hits.items() if not v or v[-1] < cutoff]
            for k in stale[:1000]:
                _unauth_hits.pop(k, None)
        return True


def _anonymous_distinct(body: TelemetryIn, props: dict[str, Any]) -> str:
    distinct = (body.distinct_id or "").strip()
    if distinct:
        return distinct
    machine_id = props.get("machine_id")
    if isinstance(machine_id, str) and machine_id:
        return f"anonymous:{machine_id}"
    return "anonymous"


@router.post("/v1/cli/telemetry")
def cli_telemetry(
    body: TelemetryIn,
    request: Request,
    session: Session = Depends(get_session),
    authorization: str = Header(default=""),
):
    """Ingest one CLI analytics event. Best-effort; always 204-style ok."""
    if body.event not in ALLOWED_EVENTS:
        return {"ok": False, "error": "event_not_allowed"}

    has_bearer = bool(
        authorization.startswith("Bearer ") and authorization.removeprefix("Bearer ").strip()
    )
    if not has_bearer:
        # Throttle before any credential hashing / DB lookup.
        ip = request.client.host if request.client else "unknown"
        if not _throttle_unauth(ip):
            return {"ok": False, "error": "rate_limited"}
        props = _sanitize(body.properties or {})
        props.pop("project_id", None)
        props.pop("email", None)
        props["source"] = "cli"
        capture(_anonymous_distinct(body, props), body.event, props)
        return {"ok": True}

    props = _sanitize(body.properties or {})
    # Never trust client-supplied identity fields; bearer decides project_id.
    client_email = props.pop("email", None)
    props.pop("project_id", None)

    project_id = _project_id_from_bearer(session, authorization)
    if not project_id:
        props["source"] = "cli"
        capture(_anonymous_distinct(body, props), body.event, props)
        return {"ok": True}

    props["project_id"] = project_id
    distinct = (body.distinct_id or "").strip() or project_id
    if isinstance(client_email, str) and "@" in client_email:
        identify(client_email, {"email": client_email, "project_id": project_id})
        distinct = client_email

    props["source"] = "cli"
    capture(str(distinct), body.event, props)
    return {"ok": True}
