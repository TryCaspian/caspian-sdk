"""Credentials encryption seam (comm_gateway.crypto)."""

import json

import pytest
from comm_gateway import crypto
from cryptography.fernet import Fernet


class _Conn:
    """Minimal stand-in for a Connection row (only the column the seam touches)."""

    def __init__(self, provider_credentials=None):
        self.provider_credentials = provider_credentials


@pytest.fixture(autouse=True)
def _plaintext_by_default():
    # Reset the process-wide cipher around every test so state can't leak.
    crypto.configure_cipher("")
    yield
    crypto.configure_cipher("")


def test_encrypted_roundtrip():
    crypto.configure_cipher(Fernet.generate_key().decode())
    conn = _Conn()
    crypto.write_credentials(conn, {"bot_token": "abc", "webhook_secret": "s"})
    # Stored form is an opaque envelope, not the plaintext.
    assert set(conn.provider_credentials) == {"__enc__"}
    assert "abc" not in json.dumps(conn.provider_credentials)
    assert crypto.read_credentials(conn) == {"bot_token": "abc", "webhook_secret": "s"}


def test_plaintext_mode_when_no_key():
    conn = _Conn()
    crypto.write_credentials(conn, {"bot_token": "abc"})
    assert conn.provider_credentials == {"bot_token": "abc"}  # dev: stored as-is
    assert crypto.read_credentials(conn) == {"bot_token": "abc"}


def test_legacy_plaintext_row_reads_with_key_set():
    # A row written before encryption existed: plaintext dict already in the column.
    crypto.configure_cipher(Fernet.generate_key().decode())
    conn = _Conn(provider_credentials={"bot_token": "legacy"})
    assert crypto.read_credentials(conn) == {"bot_token": "legacy"}


def test_empty_and_none_normalize():
    conn = _Conn(provider_credentials=None)
    assert crypto.read_credentials(conn) == {}
    crypto.write_credentials(conn, {})
    assert conn.provider_credentials is None
    crypto.write_credentials(conn, None)
    assert conn.provider_credentials is None


def test_wrong_key_raises():
    crypto.configure_cipher(Fernet.generate_key().decode())
    conn = _Conn()
    crypto.write_credentials(conn, {"t": "x"})
    crypto.configure_cipher(Fernet.generate_key().decode())  # rotated to a different key
    with pytest.raises(RuntimeError):
        crypto.read_credentials(conn)


def test_encrypted_present_but_no_key_raises():
    crypto.configure_cipher(Fernet.generate_key().decode())
    conn = _Conn()
    crypto.write_credentials(conn, {"t": "x"})
    crypto.configure_cipher("")  # key removed while ciphertext remains
    with pytest.raises(RuntimeError):
        crypto.read_credentials(conn)
