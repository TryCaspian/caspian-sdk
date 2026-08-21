# SMS bot (Python)

Inbound is a Twilio **form** webhook signed with `X-Twilio-Signature`.
Catalog inbound is webhook — `cx.handle("sms", …)` behind `examples.serve.serve()`,
not `listen()`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

SMS plans `Post`, `Reply`, and `SendMedia` only. No buttons, pin, or react.

## Self-host `add()` and `bot_token`

`channels.add("sms", via="self-host", …)` currently requires `bot_token` for
every catalog row, including SMS. This example passes `bot_token="local"` so
paperwork succeeds. The real secrets are `account_sid`, `auth_token`,
`from_number`, and `webhook_url`.

`twilio_sig` fail-closes without `webhook_url` + `auth_token`. The URL in
`add()` **must** be the public URL Twilio signed (the same value as
`SMS_WEBHOOK_URL`).

## Run

Point Twilio's Messaging webhook at a public HTTPS URL (ngrok / cloudflared)
that forwards to this process.

```bash
export TWILIO_ACCOUNT_SID='…'
export TWILIO_AUTH_TOKEN='…'
export TWILIO_FROM='+1…'
export SMS_WEBHOOK_URL='https://…/sms'
export PORT=8080

cd packages/python
uv run python ../../examples/sms/bot.py
```

Text `/help` to the Twilio number. Ctrl+C stops the process.
