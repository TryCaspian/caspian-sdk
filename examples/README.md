# Self-host examples

One Python example per catalog adapter. Handlers live in `examples/<channel>/app.py`
as `register(cx)`. The process file (`bot.py`, or `hosted.py` for hosted Telegram)
only does paperwork: `channels.add(via="self-host", …)` plus the inbound loop.

Webhook channels share [`serve.py`](serve.py) — POST → `cx.handle(channel, body,
headers)` with optional Meta GET challenge, X CRC, or TwiML response. Socket
channels (Discord, Slack) use `cx.listen()` instead.

Per-channel run instructions and provider setup are in each `examples/<channel>/README.md`.
Copy `.env.example` to `.env` and fill in secrets (never commit tokens).

**Hosted-all-channels is out of scope for this tree.** Telegram hosted already
exists as `examples/telegram/hosted.py` (same handlers, gateway owns inbound).
See the [self-host adapter examples plan](../docs/superpowers/plans/2026-08-21-self-host-adapter-examples.md).

## Index

| Channel | Script | Inbound verb | Required env |
|---|---|---|---|
| [discord](discord/) | `bot.py` | `listen("discord")` | `DISCORD_BOT_TOKEN` |
| [slack](slack/) | `bot.py` | `listen("slack")` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` |
| [telegram](telegram/) (self-host) | `bot.py` | `handle("telegram", …)` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_URL`, `PORT` (`TELEGRAM_WEBHOOK_SECRET` optional) |
| [telegram](telegram/) (hosted) | `hosted.py` | `run()` → `handle("gateway", …)` | `TELEGRAM_BOT_TOKEN`, `CASPIAN_API_KEY` |
| [email](email/) | `bot.py` | `handle("email", …)` via `serve()` | `EMAIL_FROM`, `PORT` |
| [sms](sms/) | `bot.py` | `handle("sms", …)` via `serve()` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `SMS_WEBHOOK_URL`, `PORT` |
| [voice](voice/) | `bot.py` | `handle("voice", …)` via `serve(twiml=True)` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `VOICE_WEBHOOK_URL`, `PORT` |
| [whatsapp](whatsapp/) | `bot.py` | `handle("whatsapp", …)` via `serve(verify_token=…)` | `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `PORT` |
| [messenger](messenger/) | `bot.py` | `handle("messenger", …)` via `serve(verify_token=…)` | `MESSENGER_PAGE_ACCESS_TOKEN`, `MESSENGER_APP_SECRET`, `MESSENGER_VERIFY_TOKEN`, `PORT` |
| [imessage](imessage/) | `bot.py` | `handle("imessage", …)` via `serve()` | `IMESSAGE_API_KEY`, `IMESSAGE_WEBHOOK_SECRET`, `IMESSAGE_RELAY_URL`, `PORT` |
| [linear](linear/) | `bot.py` | `handle("linear", …)` via `serve()` | `LINEAR_API_KEY`, `LINEAR_WEBHOOK_SECRET`, `PORT` |
| [x](x/) | `bot.py` | `handle("x", …)` via `serve(consumer_secret=…)` | `X_BEARER_TOKEN`, `X_CONSUMER_SECRET`, `PORT` |

Run from `packages/python` so the `caspian` package resolves:

```bash
cd packages/python
uv run python ../../examples/<channel>/bot.py
```

Socket channels need optional extras: `uv sync --extra discord` or `--extra slack-socket`.
