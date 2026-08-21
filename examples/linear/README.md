# Linear bot (Python)

Inbound is a Linear webhook signed with `Linear-Signature` (HMAC-SHA256 hex of
the body, no prefix). Catalog inbound is webhook —
`cx.handle("linear", …)` behind `examples.serve.serve()`, not `listen()`.

The adapter plans GraphQL `commentCreate` for `Post` and `Reply`. Handlers live
in `app.py`. `bot.py` only adds the channel and holds HTTP.

Linear has no media or buttons. This example posts `/help` and echoes
everything else as issue comments.

## Self-host `add()` and `bot_token`

`channels.add("linear", via="self-host", …)` currently requires `bot_token`
for every catalog row, including Linear. This example passes
`bot_token="local"` so paperwork succeeds. The real secrets are `api_key`
(Authorization on outbound GraphQL) and `webhook_secret` (inbound HMAC).

HMAC fail-closes without `webhook_secret`.

## Run

In Linear, create a personal API key and a webhook for Comment events. Point
the webhook URL at a public HTTPS URL (ngrok / cloudflared) that forwards
here. Use the same signing secret Linear puts on `Linear-Signature`.

```bash
export LINEAR_API_KEY='…'
export LINEAR_WEBHOOK_SECRET='…'
export PORT=8080

cd packages/python
uv run python ../../examples/linear/bot.py
```

Comment `/help` on an issue. Ctrl+C stops the process.
