import base64
import hashlib
import hmac
import json

from examples.serve import challenge_response, crc_response


def test_meta_subscribe_challenge_is_plain_text() -> None:
    body, status, content_type = challenge_response(
        {"hub.mode": ["subscribe"], "hub.verify_token": ["tok"], "hub.challenge": ["abc"]},
        verify_token="tok",
    )
    assert status == 200
    assert body == b"abc"
    assert content_type == "text/plain"


def test_x_crc_response_token() -> None:
    secret = "cons"
    token = "crc"
    body, status, content_type = crc_response(token, consumer_secret=secret)
    digest = hmac.new(secret.encode(), token.encode(), hashlib.sha256).digest()
    expected = "sha256=" + base64.b64encode(digest).decode()
    assert status == 200
    assert json.loads(body) == {"response_token": expected}
    assert content_type == "application/json"


def test_crc_response_empty_secret_is_forbidden() -> None:
    body, status, content_type = crc_response("crc", consumer_secret="")
    assert status == 403
    assert body == b""
    assert content_type == "text/plain"
