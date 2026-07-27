"""Short-lived signed tokens for the WhatsApp Embedded Signup launcher.

The launcher runs in a browser and must not carry a project's API key. Instead an
authenticated request mints a signed, expiring token that binds the onboarding
session to a project + scope; the launcher echoes it back to the exchange endpoint,
which verifies it. HMAC-SHA256 over a compact JSON payload — no external deps.
"""

import base64
import hashlib
import hmac
import json
import time


class SessionError(Exception):
    """The onboarding session token is missing, malformed, tampered, or expired."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(secret: str, data: dict, ttl: int = 900) -> str:
    """Sign `data` (plus an expiry `ttl` seconds out) into a `payload.sig` token."""
    body = {**data, "exp": int(time.time()) + ttl}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify(secret: str, token: str) -> dict:
    """Return the signed payload, or raise SessionError if invalid/expired."""
    payload, _, sig = token.partition(".")
    if not payload or not sig:
        raise SessionError("malformed session token")
    expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise SessionError("bad session signature")
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SessionError("unreadable session payload") from exc
    if data.get("exp", 0) < time.time():
        raise SessionError("session expired")
    return data
