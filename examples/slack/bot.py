import os

from app import register
from caspian import Caspian

token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
app_token = os.environ.get("SLACK_APP_TOKEN", "").strip()
if not token or not app_token:
    raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN (xapp-), then rerun.")

cx = Caspian()
cx.channels.add(
    "slack",
    via="self-host",
    bot_token=token,
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
    app_token=app_token,
)
register(cx)

if __name__ == "__main__":
    cx.listen("slack")
