"""Telegram hosted by Caspian. Same handlers as bot.py; the gateway owns inbound.

Hosted does not mint a BotFather bot. You still pass TELEGRAM_BOT_TOKEN.
This process never sees a Telegram Update: cx.run() polls GET /v1/events and
feeds each payload to handle("gateway", …).
"""

from __future__ import annotations

import os

from app import register
from caspian import Caspian

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
api_key = os.environ.get("CASPIAN_API_KEY", "").strip()
if not token:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN (BotFather → /newbot), then rerun.")
if not api_key:
    raise SystemExit("Set CASPIAN_API_KEY, then rerun.")

cx = Caspian(api_key=api_key)
cx.channels.add("telegram", bot_token=token)
register(cx)

if __name__ == "__main__":
    print("hosted telegram — polling gateway /v1/events", flush=True)
    cx.run()
