import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

page_access_token = os.environ.get("MESSENGER_PAGE_ACCESS_TOKEN", "").strip()
app_secret = os.environ.get("MESSENGER_APP_SECRET", "").strip()
verify_token = os.environ.get("MESSENGER_VERIFY_TOKEN", "").strip()
if not page_access_token or not app_secret or not verify_token:
    raise SystemExit(
        "Set MESSENGER_PAGE_ACCESS_TOKEN, MESSENGER_APP_SECRET, "
        "and MESSENGER_VERIFY_TOKEN, then rerun."
    )

cx = Caspian()
cx.channels.add(
    "messenger",
    via="self-host",
    page_access_token=page_access_token,
    app_secret=app_secret,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "messenger", verify_token=verify_token, port=port)
