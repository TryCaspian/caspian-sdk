import os
import sys
from pathlib import Path

from app import register
from caspian import Caspian

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import serve  # noqa: E402

from_address = os.environ.get("EMAIL_FROM", "").strip()
if not from_address:
    raise SystemExit("Set EMAIL_FROM, then rerun.")

cx = Caspian()
cx.channels.add(
    "email",
    via="self-host",
    from_address=from_address,
    bot_token="local",
)
register(cx)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    serve(cx, "email", port=port)
