# Telegram bot (Python)

Same handlers in `app.py`. Two processes:

| Script | Who owns Telegram's webhook | Inbound verb |
|---|---|---|
| `bot.py` | you (self-host) | `cx.handle("telegram", …)` behind an HTTP server |
| `hosted.py` | Caspian gateway | `cx.run()` → `handle("gateway", …)` |

Hosted still needs a BotFather token. It does not mint a bot.

## Self-host webhook

```bash
export TELEGRAM_BOT_TOKEN='…'
export TELEGRAM_WEBHOOK_URL='https://…'   # ngrok / cloudflared
export TELEGRAM_WEBHOOK_SECRET='…'        # optional; generated if omitted
export PORT=8080

cd packages/python
uv run python ../../examples/telegram/bot.py
```

`channels.add(..., webhook_url=...)` registers that URL with Telegram. Poll is
commented in `bot.py` if you have no public URL. Poll and webhook cannot both
be active (`deleteWebhook` first).

## Hosted

```bash
export TELEGRAM_BOT_TOKEN='…'
export CASPIAN_API_KEY='…'

cd packages/python
uv run python ../../examples/telegram/hosted.py
```

Send `/help` in the chat. Ctrl+C stops either process.
