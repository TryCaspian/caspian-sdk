# Messenger bot (Python)

Inbound is a Meta Page webhook signed with `X-Hub-Signature-256`.
Catalog inbound is webhook — `cx.handle("messenger", …)` behind
`examples.serve.serve(..., verify_token=…)`, not `listen()`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

Messenger plans `Post` (including a button menu), `Typing`, and echo.
No pin, react, edit, or delete.

## Self-host `add()` and `bot_token`

`channels.add("messenger", via="self-host", …)` currently requires `bot_token`
for every catalog row, including Messenger. This example passes
`bot_token="local"` so paperwork succeeds. The real secrets are
`page_access_token` and `app_secret`.

HMAC fail-closes without `app_secret`. Subscribe handshake is GET
`hub.mode=subscribe` + `hub.verify_token` + `hub.challenge` on the same
HTTP front (`verify_token=` is `MESSENGER_VERIFY_TOKEN`).

## Run

In Meta Developer Console, add the Messenger product, copy the page access
token and app secret, and set a verify token. Point the webhook callback URL
at a public HTTPS URL (ngrok / cloudflared) that forwards here. Subscribe
the `messages` field.

```bash
export MESSENGER_PAGE_ACCESS_TOKEN='…'
export MESSENGER_APP_SECRET='…'
export MESSENGER_VERIFY_TOKEN='…'
export PORT=8080

cd packages/python
uv run python ../../examples/messenger/bot.py
```

Message `/help` to the Page. Ctrl+C stops the process.
