import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
from_number = os.environ.get("TWILIO_FROM", "").strip()
webhook_url = os.environ.get("SMS_WEBHOOK_URL", "").strip()
if not account_sid or not auth_token or not from_number or not webhook_url:
    raise SystemExit(
        "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, and SMS_WEBHOOK_URL, then rerun."
    )

cx = Caspian()
cx.channels.add(
    "sms",
    via="self-host",
    account_sid=account_sid,
    auth_token=auth_token,
    from_number=from_number,
    webhook_url=webhook_url,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "sms", port=port)
