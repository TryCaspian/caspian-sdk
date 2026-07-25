"""At-rest encryption for per-connection provider credentials.

Credentials — bot tokens, WABA access tokens, per-connection webhook secrets —
live in the `Connection.provider_credentials` JSON column. In production they are
encrypted with a process-wide Fernet key (`COMM_CREDENTIALS_KEY`); with no key
configured they are stored as plaintext (dev/test only).

The storage envelope lets the two coexist and lets existing plaintext rows upgrade
transparently on their next write: an encrypted value is the single-key dict
`{"__enc__": "<fernet-token>"}`, anything else is read as plaintext. Call
`configure_cipher()` once at startup (done by `create_app` and the worker); the
read/write seam below is what every call site uses instead of touching the column.
"""

import json

from cryptography.fernet import Fernet, InvalidToken

_ENVELOPE_KEY = "__enc__"
_cipher: Fernet | None = None


def configure_cipher(key: str) -> None:
    """Install the process-wide credentials cipher. Empty key ⇒ plaintext mode."""
    global _cipher
    _cipher = Fernet(key.encode()) if key else None


def _encrypt(data: dict) -> dict:
    if _cipher is None:
        return dict(data)
    blob = json.dumps(data, separators=(",", ":")).encode()
    return {_ENVELOPE_KEY: _cipher.encrypt(blob).decode()}


def _decrypt(stored: dict) -> dict:
    if not isinstance(stored, dict) or _ENVELOPE_KEY not in stored:
        return dict(stored) if stored else {}  # legacy plaintext (or empty)
    if _cipher is None:
        raise RuntimeError(
            "encrypted credentials present but COMM_CREDENTIALS_KEY is not set"
        )
    try:
        return json.loads(_cipher.decrypt(stored[_ENVELOPE_KEY].encode()))
    except InvalidToken as exc:
        raise RuntimeError(
            "credentials failed to decrypt (wrong COMM_CREDENTIALS_KEY?)"
        ) from exc


def read_credentials(connection) -> dict:
    """Decrypt and return a connection's credentials as a plain dict (never None)."""
    return _decrypt(connection.provider_credentials or {})


def write_credentials(connection, data: dict | None) -> None:
    """Encrypt (when a key is configured) and store credentials on the connection."""
    connection.provider_credentials = _encrypt(data) if data else None


def encrypt_credentials(data: dict | None) -> dict | None:
    """Encrypt a credentials dict for storage at construction time (or None)."""
    return _encrypt(data) if data else None
