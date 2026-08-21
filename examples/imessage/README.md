# iMessage bot (Python)

Inbound is a relay webhook signed with `X-Relay-Signature` (HMAC-SHA256 hex of
the body, no prefix). Catalog inbound is webhook —
`cx.handle("imessage", …)` behind `examples.serve.serve()`, not `listen()`.

The adapter speaks the HTTP-JSON bridge (BlueBubbles / Sendblue-style), not
Apple. Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

iMessage plans `Post`, `Reply`, and `SendMedia`. No buttons, pin, typing,
edit, or delete. This example posts `/help` and echoes everything else.

## Self-host `add()` and `bot_token`

`channels.add("imessage", via="self-host", …)` currently requires `bot_token`
for every catalog row, including iMessage. This example passes
`bot_token="local"` so paperwork succeeds. The real secrets are `api_key`
(Bearer on outbound relay calls) and `webhook_secret` (inbound HMAC).
`relay_url` is the bridge base URL.

HMAC fail-closes without `webhook_secret`.

## Run

Point the relay's webhook at a public HTTPS URL (ngrok / cloudflared) that
forwards here. Set the same HMAC secret the relay uses to sign `X-Relay-Signature`.

```bash
export IMESSAGE_API_KEY='…'
export IMESSAGE_WEBHOOK_SECRET='…'
export IMESSAGE_RELAY_URL='https://relay.example'
export PORT=8080

cd packages/python
uv run python ../../examples/imessage/bot.py
```

Message `/help` from iMessage. Ctrl+C stops the process.
