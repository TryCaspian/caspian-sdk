import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from examples.serve import challenge_response


def test_meta_subscribe_challenge_is_plain_text() -> None:
    body, status, content_type = challenge_response(
        {"hub.mode": ["subscribe"], "hub.verify_token": ["tok"], "hub.challenge": ["abc"]},
        verify_token="tok",
    )
    assert status == 200
    assert body == b"abc"
    assert content_type == "text/plain"
