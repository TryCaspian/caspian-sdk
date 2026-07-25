"""Graph API calls for Meta WhatsApp Embedded Signup.

Three server-side steps turn the browser popup's authorization `code` into a
usable, gateway-owned WhatsApp number:

1. exchange_code   — code -> a business access token (needs the app id/secret).
2. subscribe_app   — subscribe our app to the customer's WABA so its numbers'
                     inbound messages are delivered to our webhook.
3. register_number — register the phone number on Cloud API (idempotent; a number
                     already registered is treated as success).

Kept as thin functions over a caller-supplied httpx client so the provider and
the onboarding route can share them and tests can drive them with a mock transport.
"""

import httpx


class MetaOnboardingError(Exception):
    """A Graph API onboarding step failed."""


def exchange_code(
    client: httpx.Client, app_id: str, app_secret: str, code: str
) -> str:
    """Exchange the Embedded Signup `code` for a business access token."""
    r = client.get(
        "/oauth/access_token",
        params={"client_id": app_id, "client_secret": app_secret, "code": code},
    )
    if r.status_code != 200:
        raise MetaOnboardingError(f"code exchange failed: {r.status_code} {r.text}")
    token = r.json().get("access_token")
    if not token:
        raise MetaOnboardingError("code exchange returned no access_token")
    return token


def subscribe_app(client: httpx.Client, waba_id: str, access_token: str) -> None:
    """Subscribe our app to the customer's WABA (enables webhook delivery)."""
    r = client.post(
        f"/{waba_id}/subscribed_apps",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if r.status_code != 200:
        raise MetaOnboardingError(f"subscribe_app failed: {r.status_code} {r.text}")


def register_number(
    client: httpx.Client, phone_number_id: str, access_token: str, pin: str
) -> None:
    """Register the phone number for Cloud API. Idempotent: an already-registered
    number returns an error Meta tags as recoverable, which we treat as success."""
    r = client.post(
        f"/{phone_number_id}/register",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"messaging_product": "whatsapp", "pin": pin},
    )
    if r.status_code == 200:
        return
    # 133xxx errors mean "already registered" — fine for our idempotent provision.
    error_code = r.json().get("error", {}).get("code") if _is_json(r) else None
    if error_code in (133005, 133006, 133010, 133016):
        return
    raise MetaOnboardingError(f"register_number failed: {r.status_code} {r.text}")


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")
