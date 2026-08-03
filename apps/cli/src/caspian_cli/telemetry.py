"""Best-effort CLI telemetry → gateway → PostHog.

Opt out with ``CASPIAN_TELEMETRY=0`` / ``false`` / ``off`` or ``--no-telemetry``.
Never raises; never logs secrets.
"""

from __future__ import annotations

import atexit
import os
import platform
import threading
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx

DEFAULT_GATEWAY = "https://api.trycaspianai.com"

# Per-request HTTP budget and atexit flush deadline (seconds).
_POST_TIMEOUT = 0.4
_FLUSH_DEADLINE = 0.8

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
_pending: set[threading.Thread] = set()
_pending_lock = threading.Lock()


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


def _deliver(url: str, payload: dict, headers: dict) -> None:
    try:
        httpx.post(url, json=payload, headers=headers, timeout=_POST_TIMEOUT)
    except Exception:
        pass


def track(event: str, properties: dict | None = None) -> None:
    """Fire-and-forget POST to ``/v1/cli/telemetry`` (daemon thread)."""
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
    url = f"{_gateway}/v1/cli/telemetry"
    payload = {
        "event": event,
        "distinct_id": distinct_id(),
        "properties": props,
    }

    def _run() -> None:
        try:
            _deliver(url, payload, headers)
        finally:
            with _pending_lock:
                _pending.discard(threading.current_thread())

    thread = threading.Thread(target=_run, name="caspian-telemetry", daemon=True)
    with _pending_lock:
        _pending.add(thread)
    thread.start()


def _flush_pending(deadline: float) -> None:
    """Wait for in-flight deliveries up to ``deadline`` (monotonic)."""
    while True:
        with _pending_lock:
            threads = [t for t in _pending if t.is_alive()]
        if not threads:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        threads[0].join(timeout=min(0.05, remaining))


def _emit_session_ended() -> None:
    global _session_ended
    if _session_ended or _disabled:
        return
    _session_ended = True
    track("cli.session_ended", {
        "duration_ms": int((time.monotonic() - _started_at) * 1000),
    })
    _flush_pending(time.monotonic() + _FLUSH_DEADLINE)


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
