"""Signed onboarding-session tokens (comm_gateway.onboarding_token)."""

import time

import pytest
from comm_gateway import onboarding_token as ot

SECRET = "app-secret-value"


def test_mint_verify_roundtrip():
    token = ot.mint(SECRET, {"project_id": "p1", "agent_id": "a1"})
    claims = ot.verify(SECRET, token)
    assert claims["project_id"] == "p1"
    assert claims["agent_id"] == "a1"
    assert "exp" in claims


def test_wrong_secret_rejected():
    token = ot.mint(SECRET, {"project_id": "p1"})
    with pytest.raises(ot.SessionError):
        ot.verify("different-secret", token)


def test_tampered_payload_rejected():
    token = ot.mint(SECRET, {"project_id": "p1"})
    payload, _, sig = token.partition(".")
    forged = ot.mint(SECRET, {"project_id": "attacker"}).partition(".")[0]
    with pytest.raises(ot.SessionError):
        ot.verify(SECRET, f"{forged}.{sig}")


def test_malformed_rejected():
    with pytest.raises(ot.SessionError):
        ot.verify(SECRET, "not-a-token")


def test_expired_rejected():
    token = ot.mint(SECRET, {"project_id": "p1"}, ttl=-1)
    assert token  # minted fine
    with pytest.raises(ot.SessionError):
        ot.verify(SECRET, token)


def test_valid_within_ttl():
    token = ot.mint(SECRET, {"project_id": "p1"}, ttl=60)
    claims = ot.verify(SECRET, token)
    assert claims["exp"] >= int(time.time())
