"""Tests for HostedBilling: correct method+path, decoding, and error propagation."""

from __future__ import annotations

from caspian.hosted.billing import Billing, BillingLimits, HostedBilling, Usage
from caspian.hosted.client import FakeGatewayClient


def test_get_hits_billing_and_decodes() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"balance_cents": 1234, "currency": "usd", "autopay": True})
    billing = HostedBilling(client)

    result = billing.get()

    assert result.is_ok
    assert isinstance(result.value, Billing)
    assert result.value.balance_cents == 1234
    assert result.value.autopay is True
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/billing"


def test_get_uses_defaults_when_body_sparse() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"balance_cents": 500})
    result = HostedBilling(client).get()

    assert result.is_ok
    assert result.value.currency == "usd"
    assert result.value.autopay is False


def test_topup_posts_amount_and_decodes() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"balance_cents": 2000})
    result = HostedBilling(client).topup(1500)

    assert result.is_ok
    assert result.value.balance_cents == 2000
    req = client.requests[-1]
    assert req.method == "POST"
    assert req.path == "/v1/billing/topup"
    assert req.json_body == {"amount_cents": 1500}


def test_topup_propagates_insufficient_credit() -> None:
    client = FakeGatewayClient()
    client.queue_status(402, "insufficient credit")
    result = HostedBilling(client).topup(10)

    assert not result.is_ok
    assert result.error is not None
    assert result.error.tag == "InsufficientCredit"


def test_set_autopay_posts_flags() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"balance_cents": 0, "autopay": True})
    result = HostedBilling(client).set_autopay(True, threshold_cents=250)

    assert result.is_ok
    assert result.value.autopay is True
    req = client.requests[-1]
    assert req.method == "POST"
    assert req.path == "/v1/billing/autopay"
    assert req.json_body == {"enabled": True, "threshold_cents": 250}


def test_set_limits_posts_and_decodes() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"daily_cents": 5000})
    result = HostedBilling(client).set_limits(5000)

    assert result.is_ok
    assert isinstance(result.value, BillingLimits)
    assert result.value.daily_cents == 5000
    req = client.requests[-1]
    assert req.method == "POST"
    assert req.path == "/v1/billing/limits"
    assert req.json_body == {"daily_cents": 5000}


def test_usage_hits_endpoint_and_decodes() -> None:
    client = FakeGatewayClient()
    client.queue_ok({"messages": 42, "cents": 314})
    result = HostedBilling(client).usage()

    assert result.is_ok
    assert isinstance(result.value, Usage)
    assert result.value.messages == 42
    assert result.value.cents == 314
    req = client.requests[-1]
    assert req.method == "GET"
    assert req.path == "/v1/usage"


def test_usage_defaults_when_empty() -> None:
    client = FakeGatewayClient()
    client.queue_ok({})
    result = HostedBilling(client).usage()

    assert result.is_ok
    assert result.value.messages == 0
    assert result.value.cents == 0
