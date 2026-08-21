"""Named inbound verify rituals. Missing required config → False (fail closed)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable
from urllib.parse import parse_qs

from caspian.connection import Connection
from caspian.core.ports import RawInbound

Verify = Callable[[RawInbound, Connection], bool]


def unsigned(_raw: RawInbound, _conn: Connection) -> bool:
    """Explicit: this inbound has no signature scheme yet (email SNS, …)."""
    return True


def hmac_hex(
    *,
    header: str,
    secret_key: str,
    prefix: str = "",
) -> Verify:
    """HMAC-SHA256 hex of the raw body, optional prefix (``sha256=`` for Meta)."""

    def check(raw: RawInbound, conn: Connection) -> bool:
        secret = str(conn.config.get(secret_key, "") or "")
        if not secret:
            return False
        digest = hmac.new(secret.encode(), raw.body, hashlib.sha256).hexdigest()
        expected = prefix + digest
        got = raw.headers.get(header, "")
        return hmac.compare_digest(expected, got)

    return check


def hmac_b64(*, header: str, secret_key: str, prefix: str = "sha256=") -> Verify:
    """HMAC-SHA256 of the body, base64-encoded (X Account Activity)."""

    def check(raw: RawInbound, conn: Connection) -> bool:
        secret = str(conn.config.get(secret_key, "") or "")
        if not secret:
            return False
        digest = hmac.new(secret.encode(), raw.body, hashlib.sha256).digest()
        expected = prefix + base64.b64encode(digest).decode()
        got = raw.headers.get(header, "")
        return hmac.compare_digest(expected, got)

    return check


def header_equals(*, header: str, secret_key: str) -> Verify:
    """Constant-time compare of a shared secret header (Telegram secret token)."""

    def check(raw: RawInbound, conn: Connection) -> bool:
        expected = str(conn.config.get(secret_key, "") or "")
        if not expected:
            return False
        got = raw.headers.get(header, "")
        return hmac.compare_digest(expected, got)

    return check


def hmac_slack(raw: RawInbound, conn: Connection) -> bool:
    secret = str(conn.config.get("signing_secret", "") or "")
    if not secret:
        return False
    timestamp = raw.headers.get("X-Slack-Request-Timestamp", "")
    body = raw.body.decode("utf-8") if isinstance(raw.body, bytes) else str(raw.body)
    base = f"v0:{timestamp}:{body}"
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    got = raw.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(expected, got)


def twilio_sig(raw: RawInbound, conn: Connection) -> bool:
    """HMAC-SHA1 of url + sorted form params, base64 (SMS and Voice)."""
    auth_token = str(conn.config.get("auth_token", "") or "")
    webhook_url = str(conn.config.get("webhook_url", "") or "")
    if not auth_token or not webhook_url:
        return False
    signature = raw.headers.get("X-Twilio-Signature", "")
    try:
        params = parse_qs(raw.body.decode())
    except (UnicodeDecodeError, ValueError):
        return False
    payload = webhook_url
    for key in sorted(params):
        for value in params[key]:
            payload += key + value
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def discord_ed25519(raw: RawInbound, conn: Connection) -> bool:
    """Ed25519 over timestamp+body. No public_key, or no verifier → False."""
    public_key = str(conn.config.get("public_key", "") or "")
    if not public_key:
        return False
    timestamp = raw.headers.get("X-Signature-Timestamp", "")
    signature = raw.headers.get("X-Signature-Ed25519", "")
    if not timestamp or not signature:
        return False
    try:
        from nacl.encoding import HexEncoder
        from nacl.signing import VerifyKey
    except ImportError:
        return False
    try:
        key = VerifyKey(public_key, encoder=HexEncoder)
        key.verify(timestamp.encode() + raw.body, bytes.fromhex(signature))
    except Exception:
        return False
    return True
