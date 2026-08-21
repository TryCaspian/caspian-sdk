# Voice bot (Python)

Inbound is a Twilio **Voice** form webhook signed with `X-Twilio-Signature`.
Catalog inbound is webhook — `cx.handle("voice", …)` behind
`examples.serve.serve(..., twiml=True)`, not `listen()`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

Voice plans `Post` and `Reply` as TwiML `<Say>`. The HTTP layer writes that
markup back as `text/xml`; it does not dispatch an outbound API call.

## Self-host `add()` and `bot_token`

`channels.add("voice", via="self-host", …)` currently requires `bot_token` for
every catalog row, including Voice. This example passes `bot_token="local"` so
paperwork succeeds. The real secrets are `account_sid`, `auth_token`, and
`webhook_url`.

`twilio_sig` fail-closes without `webhook_url` + `auth_token`. The URL in
`add()` **must** be the public URL Twilio signed (the same value as
`VOICE_WEBHOOK_URL`).

## Run

Point Twilio's Voice webhook at a public HTTPS URL (ngrok / cloudflared)
that forwards to this process. Enable speech recognition so inbound posts
include `SpeechResult`.

```bash
export TWILIO_ACCOUNT_SID='…'
export TWILIO_AUTH_TOKEN='…'
export VOICE_WEBHOOK_URL='https://…/voice'
export PORT=8080

cd packages/python
uv run python ../../examples/voice/bot.py
```

Call the Twilio number and speak. The webhook response is TwiML that says
the transcribed text back. Ctrl+C stops the process.
