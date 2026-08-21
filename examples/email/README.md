# Email bot (Python)

Inbound is **unsigned** JSON or SES→SNS. Catalog inbound is webhook —
`cx.handle("email", …)` behind `examples.serve.serve()`, not `listen()`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds HTTP.

## Self-host `add()` and `bot_token`

`channels.add("email", via="self-host", …)` currently requires `bot_token` for
every catalog row, including email (unsigned verify still does not use it).
This example passes `bot_token="local"` so paperwork succeeds. `EMAIL_FROM` is
the real send identity (`from_address`).

## Run

```bash
export EMAIL_FROM='bot@example.com'
export PORT=8080

cd packages/python
uv run python ../../examples/email/bot.py
```

POST simplified inbound JSON the adapter already parses:

```bash
curl -sS -X POST http://127.0.0.1:8080/ \
  -H 'Content-Type: application/json' \
  -d '{"from":"a@b.c","to":"bot@example.com","subject":"x","body":"/help","message_id":"<1>"}'
```

Outbound is the smtp transport description (no live SMTP in the unit test).
Ctrl+C stops the process.
