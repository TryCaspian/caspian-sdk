import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

api_key = os.environ.get("IMESSAGE_API_KEY", "").strip()
webhook_secret = os.environ.get("IMESSAGE_WEBHOOK_SECRET", "").strip()
relay_url = os.environ.get("IMESSAGE_RELAY_URL", "").strip()
if not api_key or not webhook_secret or not relay_url:
    raise SystemExit(
        "Set IMESSAGE_API_KEY, IMESSAGE_WEBHOOK_SECRET, and IMESSAGE_RELAY_URL, then rerun."
    )

cx = Caspian()
cx.channels.add(
    "imessage",
    via="self-host",
    api_key=api_key,
    webhook_secret=webhook_secret,
    relay_url=relay_url,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "imessage", port=port)
