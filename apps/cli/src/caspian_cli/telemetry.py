"""Best-effort CLI telemetry → gateway → PostHog.

Opt out with ``CASPIAN_TELEMETRY=0`` / ``false`` / ``off`` or ``--no-telemetry``.
Never raises; never logs secrets.
"""

from __future__ import annotations

import atexit
import os
import platform
import sys
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx

DEFAULT_GATEWAY = "https://api.trycaspianai.com"

_session_id = str(uuid.uuid4())
_started_at = time.monotonic()
_disabled = False
_gateway = DEFAULT_GATEWAY
_api_key: str | None = None
_email: str | None = None
_project_id: str | None = None
_machine_id: str | None = None
_atexit_registered = False
_session_ended = False


def _env_disabled() -> bool:
    return os.environ.get("CASPIAN_TELEMETRY", "1").strip().lower() in {
        "0", "false", "no", "off",
    }


def _cli_version() -> str:
    try:
        return version("caspian-cli")
    except PackageNotFoundError:
        return "0.0.0"


def machine_id() -> str:
    """Stable anonymous id for this machine (stored under ~/.config/caspian/)."""
    global _machine_id
    if _machine_id:
        return _machine_id
    path = Path.home() / ".config" / "caspian" / "machine_id"
    try:
        if path.exists():
            mid = path.read_text().strip()
            if mid:
                _machine_id = mid
                return mid
        path.parent.mkdir(parents=True, exist_ok=True)
        mid = uuid.uuid4().hex
        path.write_text(mid + "\n")
        _machine_id = mid
        return mid
    except OSError:
        _machine_id = uuid.uuid4().hex
        return _machine_id


def distinct_id() -> str:
    if _email:
        return _email
    if _project_id:
        return _project_id
    return f"anonymous:{machine_id()}"


def configure(
    *,
    disabled: bool = False,
    gateway: str | None = None,
    api_key: str | None = None,
) -> None:
    global _disabled, _gateway, _api_key, _atexit_registered
    _disabled = disabled or _env_disabled()
    if gateway:
        _gateway = gateway.rstrip("/")
    if api_key:
        _api_key = api_key
    if not _atexit_registered:
        atexit.register(_emit_session_ended)
        _atexit_registered = True


def set_identity(*, email: str | None = None, project_id: str | None = None,
                 api_key: str | None = None) -> None:
    global _email, _project_id, _api_key
    if email:
        _email = email
    if project_id:
        _project_id = project_id
    if api_key:
        _api_key = api_key


def set_gateway(gateway: str) -> None:
    global _gateway
    _gateway = gateway.rstrip("/")


def track(event: str, properties: dict | None = None) -> None:
    """Fire-and-forget POST to ``/v1/cli/telemetry``."""
    if _disabled:
        return
    props = {
        "cli_session_id": _session_id,
        "cli_version": _cli_version(),
        "os": platform.system(),
        "python_version": platform.python_version(),
        "machine_id": machine_id(),
    }
    if _project_id:
        props["project_id"] = _project_id
    if _email:
        props["email"] = _email
    if properties:
        for k, v in properties.items():
            if isinstance(v, (str, int, float, bool)) or (
                isinstance(v, list) and all(isinstance(x, str) for x in v)
            ):
                props[k] = v
    headers = {}
    if _api_key:
        headers["Authorization"] = f"Bearer {_api_key}"
    try:
        httpx.post(
            f"{_gateway}/v1/cli/telemetry",
            json={
                "event": event,
                "distinct_id": distinct_id(),
                "properties": props,
            },
            headers=headers,
            timeout=2.0,
        )
    except Exception:
        pass


def _emit_session_ended() -> None:
    global _session_ended
    if _session_ended or _disabled:
        return
    _session_ended = True
    track("cli.session_ended", {
        "duration_ms": int((time.monotonic() - _started_at) * 1000),
    })


def argv_flags(args) -> list[str]:
    """Non-secret flag names present on the argparse namespace."""
    flags: list[str] = []
    secret_keys = {"bot_token", "api_key", "text", "password", "token", "secret"}
    for key, value in vars(args).items():
        if key in {"func", "command"} or key in secret_keys:
            continue
        if value is True:
            flags.append(key)
        elif value not in (None, False, "", []):
            if key not in secret_keys:
                flags.append(key)
    return flags


def session_id() -> str:
    return _session_id
