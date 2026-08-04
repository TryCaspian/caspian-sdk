"""Instagram/Messenger adapter (meta_messaging.py): payload normalization."""

import json

from comm_gateway.providers.meta_messaging import parse_messaging_webhook


def _payload(messaging: list[dict]) -> bytes:
    return json.dumps({"entry": [{"id": "PAGE1", "messaging": messaging}]}).encode()


def test_parse_messaging_webhook_skips_entry_missing_sender():
    """A malformed messaging entry (missing "sender", or "sender" with no
    "id") must be skipped, not raise a KeyError - a well-formed entry in the
    same batch still comes through."""
    payload = _payload(
        [
            {"message": {"mid": "m1", "text": "hi"}},
            {"sender": {}, "message": {"mid": "m2", "text": "hi"}},
            {"sender": {"id": "USER1"}, "message": {"mid": "m3", "text": "hello"}},
        ]
    )
    msgs = parse_messaging_webhook(payload, page_id="PAGE1", channel="instagram")
    assert len(msgs) == 1
    assert msgs[0].sender_address == "USER1"
    assert msgs[0].text == "hello"
