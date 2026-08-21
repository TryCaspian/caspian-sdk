# Telegram bot (Python)

One script: `bot.py`. Self-host webhook: Telegram POSTs to a public HTTPS URL,
this process calls `cx.handle`. `channels.add(..., webhook_url=...)` registers
that URL with Telegram. Poll is commented at the bottom if you have no public URL.

```bash
export TELEGRAM_BOT_TOKEN='…'          # BotFather → /newbot
export TELEGRAM_WEBHOOK_URL='https://…' # ngrok / cloudflared, HTTPS required
export TELEGRAM_WEBHOOK_SECRET='…'      # optional; generated if omitted
export PORT=8080                        # local listen; tunnel this

cd packages/python
uv run python ../../examples/telegram/bot.py
```

Send `/help` in the chat. Ctrl+C stops the server. To go back to poll, Telegram
needs `deleteWebhook` first (poll and webhook cannot both be active).

## Hosted Telegram

Yes. Telegram is a hosted channel; the gateway owns inbound. Hosted does **not**
mint a BotFather bot — you still pass `bot_token`. Then `cx.run()` (or
`cx.handle("gateway", …)`), not `cx.handle("telegram", …)`.

```python
cx = Caspian(api_key=os.environ["CASPIAN_API_KEY"])
cx.channels.add("telegram", bot_token=token)  # via defaults to hosted
cx.run()
```
