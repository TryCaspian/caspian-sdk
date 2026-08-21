# Telegram bot (TypeScript)

The TypeScript twin of [`../telegram`](../telegram): same commands, same two
processes, so a change to either SDK can be checked against the same chat.

| Script | Who owns Telegram's webhook | Inbound verb |
|---|---|---|
| `bot.ts` | you (self-host) | `cx.handle("telegram", …)` behind `Bun.serve` |
| `hosted.ts` | Caspian gateway | `cx.run({ apiKey })` |

Hosted still needs a BotFather token. It does not mint a bot.

## Self-host webhook

```bash
cd examples/telegram-ts
bun install
export TELEGRAM_BOT_TOKEN='…'
export TELEGRAM_WEBHOOK_SECRET='…'      # optional
bun run bot.ts
```

Unlike the Python SDK, `channels.add()` does not register the webhook with
Telegram yet — do it once yourself with the server exposed (ngrok /
cloudflared):

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://…/" -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

## Hosted

```bash
export TELEGRAM_BOT_TOKEN='…'
export CASPIAN_API_KEY='…'
bun run hosted.ts
```

Send `/help` in the chat. Ctrl+C stops either process.
