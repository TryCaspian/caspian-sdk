import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

bearer_token = os.environ.get("X_BEARER_TOKEN", "").strip()
consumer_secret = os.environ.get("X_CONSUMER_SECRET", "").strip()
if not bearer_token or not consumer_secret:
    raise SystemExit("Set X_BEARER_TOKEN and X_CONSUMER_SECRET, then rerun.")

cx = Caspian()
cx.channels.add(
    "x",
    via="self-host",
    bearer_token=bearer_token,
    consumer_secret=consumer_secret,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "x", consumer_secret=consumer_secret, port=port)
