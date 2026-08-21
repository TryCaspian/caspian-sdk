import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
app_secret = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
verify_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()
if not access_token or not phone_number_id or not app_secret or not verify_token:
    raise SystemExit(
        "Set WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, "
        "WHATSAPP_APP_SECRET, and WHATSAPP_VERIFY_TOKEN, then rerun."
    )

cx = Caspian()
cx.channels.add(
    "whatsapp",
    via="self-host",
    access_token=access_token,
    phone_number_id=phone_number_id,
    app_secret=app_secret,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "whatsapp", verify_token=verify_token, port=port)
