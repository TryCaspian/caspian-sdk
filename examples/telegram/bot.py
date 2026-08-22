"""Telegram self-host webhook. Handlers live in app.py."""

from __future__ import annotations

import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from app import register
from caspian import Caspian

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
# WARNING: Generating a random secret on startup means the webhook secret changes
# every restart. On ephemeral platforms (Heroku, Render, Railway), this breaks
# webhook delivery until re-registered. For production, persist this in your .env:
#   TELEGRAM_WEBHOOK_SECRET=<a-stable-random-string>
webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(24)
if not token:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN (BotFather → /newbot), then rerun.")
if not webhook_url:
    raise SystemExit(
        "Set TELEGRAM_WEBHOOK_URL to a public HTTPS URL (ngrok / cloudflared), then rerun."
    )

cx = Caspian()
register(cx)


class _Webhook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        results = cx.handle("telegram", body, {k: v for k, v in self.headers.items()})
        for result in results:
            if not result.is_ok:
                print(result.error, flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        print(format % args, flush=True)


if __name__ == "__main__":
    parsed = urlparse(webhook_url)
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("127.0.0.1", port), _Webhook)
    cx.channels.add(
        "telegram",
        via="self-host",
        bot_token=token,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )
    print(f"webhook {webhook_url}  local :{port}{parsed.path or '/'}", flush=True)
    # cx.poll("telegram")  # no public URL: long-poll getUpdates instead
    server.serve_forever()
