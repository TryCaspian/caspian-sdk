# X bot (Python)

Inbound is an Account Activity webhook signed with
`X-Twitter-Webhooks-Signature` (HMAC-SHA256 of the body, base64, `sha256=`
prefix). Catalog inbound is webhook — `cx.handle("x", …)` behind
`examples.serve.serve(..., consumer_secret=…)`, not `listen()`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

X plans `Post` and `Reply` (tweets and DMs). This example posts `/help` and
echoes everything else via `thread.post` only.

CRC is a GET on the same HTTP front: `crc_token` → JSON
`{"response_token": "sha256=<hmac>"}` signed with `consumer_secret`.
`handle()` never sees that GET.

## Self-host `add()` and `bot_token`

`channels.add("x", via="self-host", …)` currently requires `bot_token` for
every catalog row, including X. This example passes `bot_token="local"` so
paperwork succeeds. The real secrets are `bearer_token` (Authorization on
outbound v2 calls) and `consumer_secret` (inbound HMAC and CRC).

HMAC fail-closes without `consumer_secret`.

## Run

In the X Developer Portal, create an app with Account Activity, copy the
bearer token and consumer secret, and register a webhook. Point the webhook
URL at a public HTTPS URL (ngrok / cloudflared) that forwards here. X will
GET `crc_token` to verify the URL, then POST DM and tweet events.

```bash
export X_BEARER_TOKEN='…'
export X_CONSUMER_SECRET='…'
export PORT=8080

cd packages/python
uv run python ../../examples/x/bot.py
```

DM or mention `/help`. Ctrl+C stops the process.
