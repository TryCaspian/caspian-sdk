"""X-Twilio-Signature verification for inbound Twilio webhooks.

Twilio signs every webhook it sends: the header is base64(HMAC-SHA1) over the
exact request URL concatenated with each POST field appended in sorted key order,
keyed by the account's auth token. Verifying it is the only thing standing between
the public webhook endpoint and a forged inbound message, so the Twilio adapters
call this before trusting a payload. See:
https://www.twilio.com/docs/usage/security#validating-requests
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from urllib.parse import parse_qs

from .base import WebhookVerificationError


def verify_twilio_signature(
    auth_token: str, url: str, params: Mapping[str, str], signature: str
) -> bool:
    """Return True iff `signature` is a valid X-Twilio-Signature for the request.

    `url` must be the exact URL Twilio was configured to POST to (scheme, host,
    path, and any query string), otherwise the HMAC will not match.
    """
    if not (auth_token and signature):
        return False
    signed = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), signed.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def verify_twilio_webhook(
    auth_token: str, verify_url: str, payload: bytes, headers: Mapping[str, str]
) -> None:
    """Raise WebhookVerificationError if an inbound Twilio webhook is unsigned or
    forged. A no-op when `verify_url` is empty (verification not configured), so
    existing deployments are unaffected until they opt in.
    """
    if not verify_url:
        return
    signature = headers.get("X-Twilio-Signature") or headers.get("x-twilio-signature", "")
    form = {k: v[0] for k, v in parse_qs(payload.decode()).items()}
    if not verify_twilio_signature(auth_token, verify_url, form, signature):
        raise WebhookVerificationError("invalid or missing X-Twilio-Signature")
