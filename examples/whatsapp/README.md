# WhatsApp bot (Python)

Inbound is a Meta Cloud API JSON webhook signed with `X-Hub-Signature-256`.
Catalog inbound is webhook — `cx.handle("whatsapp", …)` behind
`examples.serve.serve(..., verify_token=…)`, not `listen()`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

WhatsApp plans `Post` (including a button menu), `Reply`, `SendMedia`, and
`React`. No pin, typing, edit, or delete.

## Self-host `add()` and `bot_token`

`channels.add("whatsapp", via="self-host", …)` currently requires `bot_token`
for every catalog row, including WhatsApp. This example passes
`bot_token="local"` so paperwork succeeds. The real secrets are
`access_token`, `phone_number_id`, and `app_secret`.

HMAC fail-closes without `app_secret`. Subscribe handshake is GET
`hub.mode=subscribe` + `hub.verify_token` + `hub.challenge` on the same
HTTP front (`verify_token=` is `WHATSAPP_VERIFY_TOKEN`).

## Run

In Meta Developer Console, add the WhatsApp product, copy the access token,
phone number ID, and app secret, and set a verify token. Point the webhook
callback URL at a public HTTPS URL (ngrok / cloudflared) that forwards here.
Subscribe the `messages` field.

```bash
export WHATSAPP_ACCESS_TOKEN='…'
export WHATSAPP_PHONE_NUMBER_ID='…'
export WHATSAPP_APP_SECRET='…'
export WHATSAPP_VERIFY_TOKEN='…'
export PORT=8080

cd packages/python
uv run python ../../examples/whatsapp/bot.py
```

Message `/help` to the business number. Ctrl+C stops the process.
