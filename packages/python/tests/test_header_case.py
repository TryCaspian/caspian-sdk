"""Header names are case insensitive, so signature checks survive any framework.

RFC 9110 makes HTTP field names case insensitive. Frameworks disagree about
what casing they hand you: Starlette's dict(request.headers), Express and Bun
lowercase them; http.server and Flask preserve the client's. Adapters ask for
"X-Slack-Signature". Before this, half the ecosystem got every request rejected
as unverified, with no error beyond "signature verification failed".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from caspian.adapters.linear import LinearAdapter
from caspian.adapters.slack import SlackAdapter
from caspian.adapters.telegram import TelegramAdapter
from caspian.core.ports import Connection, Headers, RawInbound

BODY = json.dumps({"type": "event_callback", "event": {"type": "message"}}).encode()


def test_headers_lookup_ignores_case() -> None:
    headers = Headers({"X-Slack-Signature": "v0=abc"})
    assert headers.get("x-slack-signature") == "v0=abc"
    assert headers.get("X-SLACK-SIGNATURE") == "v0=abc"
    assert "x-slack-signature" in headers
    assert headers["X-Slack-Signature"] == "v0=abc"


def test_headers_preserve_original_casing_for_round_trip() -> None:
    headers = Headers({"X-Slack-Signature": "v0=abc"})
    assert list(headers.keys()) == ["X-Slack-Signature"]


def test_missing_header_still_returns_the_default() -> None:
    headers = Headers({"a": "1"})
    assert headers.get("nope", "") == ""
    with pytest.raises(KeyError):
        headers["nope"]


@pytest.mark.parametrize("case", ["exact", "lower", "upper"])
def test_slack_signature_verifies_in_any_header_case(case: str) -> None:
    secret = "s3cr3t"
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(
        secret.encode(), f"v0:{stamp}:{BODY.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    sent = {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature}
    if case == "lower":
        sent = {k.lower(): v for k, v in sent.items()}
    elif case == "upper":
        sent = {k.upper(): v for k, v in sent.items()}

    adapter = SlackAdapter()
    connection = Connection(id="c", channel="slack", config={"signing_secret": secret})
    assert adapter.verify(RawInbound(BODY, sent), connection) is True


def test_slack_still_rejects_a_wrong_signature() -> None:
    """Case folding must not turn verification into a rubber stamp."""
    adapter = SlackAdapter()
    connection = Connection(id="c", channel="slack", config={"signing_secret": "s3cr3t"})
    bad = {"x-slack-request-timestamp": "1", "x-slack-signature": "v0=deadbeef"}
    assert adapter.verify(RawInbound(BODY, bad), connection) is False


@pytest.mark.parametrize("case", ["exact", "lower"])
def test_linear_signature_verifies_in_any_header_case(case: str) -> None:
    secret = "linear-secret"
    body = json.dumps({"action": "create"}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sent = {"Linear-Signature": signature}
    if case == "lower":
        sent = {k.lower(): v for k, v in sent.items()}

    adapter = LinearAdapter()
    connection = Connection(id="c", channel="linear", config={"webhook_secret": secret})
    assert adapter.verify(RawInbound(body, sent), connection) is True


@pytest.mark.parametrize("case", ["exact", "lower"])
def test_telegram_secret_token_verifies_in_any_header_case(case: str) -> None:
    sent = {"X-Telegram-Bot-Api-Secret-Token": "tok"}
    if case == "lower":
        sent = {k.lower(): v for k, v in sent.items()}

    adapter = TelegramAdapter()
    connection = Connection(id="c", channel="telegram", config={"secret_token": "tok"})
    assert adapter.verify(RawInbound(b"{}", sent), connection) is True
