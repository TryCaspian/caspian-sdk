"""Registry conformance — every adapter satisfies the AdapterPort shape."""

from __future__ import annotations

import pytest

from caspian.adapters import REGISTRY, get_adapter

EXPECTED_CHANNELS = {
    "telegram",
    "slack",
    "discord",
    "email",
    "whatsapp",
    "messenger",
    "sms",
    "voice",
    "imessage",
    "x",
    "linear",
}


def test_registry_has_all_channels() -> None:
    assert set(REGISTRY) == EXPECTED_CHANNELS


@pytest.mark.parametrize("channel", sorted(EXPECTED_CHANNELS))
def test_adapter_conforms_to_port(channel: str) -> None:
    adapter = get_adapter(channel)

    # name matches the registry key
    assert adapter.name == channel  # type: ignore[attr-defined]

    # required methods exist and are callable
    for method in ("parse", "execute", "overlap_key", "capabilities", "verify", "format"):
        assert callable(getattr(adapter, method)), f"{channel}.{method} missing"

    # capabilities is a non-empty frozenset that at least receives or sends
    caps = adapter.capabilities()  # type: ignore[attr-defined]
    assert isinstance(caps, frozenset)
    assert caps & {"receive", "send"}


@pytest.mark.parametrize("channel", sorted(EXPECTED_CHANNELS))
def test_parse_unknown_never_raises(channel: str) -> None:
    from caspian.core.ports import RawInbound

    adapter = get_adapter(channel)
    # Empty/garbage JSON object should never raise; returns ok([]) or a DecodeError.
    result = adapter.parse(RawInbound(body=b"{}"))  # type: ignore[attr-defined]
    assert result.is_ok or result.error is not None
