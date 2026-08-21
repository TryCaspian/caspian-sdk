import os

from caspian import Caspian
from app import register

token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("Set DISCORD_BOT_TOKEN, then rerun.")

cx = Caspian()
cx.channels.add("discord", via="self-host", bot_token=token)
register(cx)

if __name__ == "__main__":
    cx.listen("discord")
