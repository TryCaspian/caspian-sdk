import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

api_key = os.environ.get("LINEAR_API_KEY", "").strip()
webhook_secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "").strip()
if not api_key or not webhook_secret:
    raise SystemExit("Set LINEAR_API_KEY and LINEAR_WEBHOOK_SECRET, then rerun.")

cx = Caspian()
cx.channels.add(
    "linear",
    via="self-host",
    api_key=api_key,
    webhook_secret=webhook_secret,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "linear", port=port)
