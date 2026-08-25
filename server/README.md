# Caspian gateway (self-host)

This is the backend that the `caspian-sdk` client talks to. It is a FastAPI
service that normalizes every channel behind one provider interface, verifies
inbound webhooks, and dispatches to your agent. The hosted version runs at
`https://api.trycaspianai.com`; this folder is the same code, so you can run
your own.

## Quick start (Docker)

From the repo root:

```bash
docker compose up
```

The gateway comes up on `http://localhost:8000` with Postgres and the in-memory
`fake` provider (no credentials). Point the SDK at it:

```python
from caspian import Caspian
cx = Caspian(base_url="http://localhost:8000", api_key="comm_dev_key_change_me")
```

## Run it locally (without Docker)

```bash
cd server
uv sync
uv run comm-gateway        # API + in-process worker on 127.0.0.1:8000
```

Defaults to SQLite (`./comm.db`) and the `fake` provider, so it boots with almost
no configuration.

## Configuration

Everything is environment driven (prefix `COMM_`). Copy the template and fill in
only what you need:

```bash
cp server/.env.example .env
```

Then set `COMM_PROVIDERS` to the channels you want and add that channel's
credentials. Bring-your-own channels (Telegram, Slack, Discord, your own
Twilio/Telnyx number) need no platform-level cost to Caspian. See
[`.env.example`](./.env.example) for every setting.

## Notes for self-hosting

- **Bring your own everything.** Each channel uses your own bot token / API keys,
  supplied per connection or via env. Nothing routes through Caspian.
- **Billing and analytics are optional.** Leave the Stripe and PostHog settings
  blank and neither runs.
- **Secrets.** Never commit a filled-in `.env`. In the hosted deployment secrets
  load from a secret store at startup; for self-host, use your own.
- **Public URL.** For OAuth redirects and inbound webhooks, set
  `COMM_PUBLIC_BASE_URL` to a URL the channels can reach (a tunnel like
  cloudflared/ngrok works for local testing).
