# Caspian

**One identity for your AI agent across every channel humans use — behind a single `on_message` handler.**

This repo is the open core of Caspian: the **channel adapters**, the **Python SDK**, and the **CLI**. Every adapter turns a platform into the same small interface — `provision` / `send` / `reply` / `parse_webhook`, with capability negotiation and real webhook verification — so the same agent code answers on every channel.

```python
from caspian_sdk import CommClient

client = CommClient()  # reads COMM_API_KEY / COMM_BASE_URL from .env
email = client.connect_email(display_name="My Agent")
print("Agent email:", email["address"])

@client.on_message
def handle(message):
    message.reply(f"You said: {message.text}")

client.listen()
```

## What's here

| Package | What it is |
|---|---|
| **`packages/adapters`** (`caspian-adapters`) | The channel adapters: Slack, Discord, Telegram (bot + user-account), Instagram DM, Facebook Messenger, X, email (AWS SES), Google Meet, GSM-modem SMS — plus in-memory fakes for every channel, for tests and local dev. Bring your own platform credentials. |
| **`sdks/python`** (`caspian-sdk`) | The Python client — one `on_message` handler, `connect_*()` per channel, `message.reply()`, behavior guides. `pip install caspian-sdk`. |
| **`apps/cli`** (`comm`) | Init a project, connect channels, tail events from a terminal. |
| **`examples/`** | Minimal runnable agents. |

## Channels

| Channel | Adapter (bring your own credentials) | Caspian hosted |
|---|---|---|
| Email | ✅ your AWS SES account | ✅ instant inbox, custom domains |
| Telegram (bot) | ✅ your @BotFather token | ✅ |
| Discord | ✅ your bot, or shared-bot OAuth | ✅ one-click install |
| Slack | ✅ your app, or shared-app OAuth | ✅ one-click install |
| Instagram DM | ✅ your Meta app + Page token | ✅ |
| Facebook Messenger | ✅ your Meta app + Page token | ✅ |
| X / Twitter | ✅ your X API app — see note below | ✅ |
| Google Meet | ✅ your Workspace service account | ✅ |
| SMS | ✅ your own GSM modem hardware | ✅ real numbers, no hardware |
| Telegram (user account) | ⚠️ opt-in — see note below | — |
| WhatsApp Business | — | ✅ one-click number onboarding |
| Phone / voice | — | ✅ |
| iMessage | — | ✅ |
| RCS | — | ✅ |

Need phone numbers, WhatsApp, or iMessage? Same API, hosted — no numbers to buy, no platform review: **[trycaspianai.com](https://trycaspianai.com)**. Hosted and third-party channels plug in through the `caspian.providers` entry-point group without forking this repo.

### Channel notes (read before you promise features)

- **X is not a free channel.** X's free API tier is write-only and heavily capped; DM send/receive requires a paid X API subscription on *your* X developer app. Budget for it.
- **Telegram user-account automation (`telegram-user`) is ToS-gray.** It drives a personal account over MTProto, which Telegram may treat as automation abuse. It requires explicit opt-in configuration; if you enable it, account bans are your risk. Don't use it for spam — that harms everyone's accounts, starting with yours.
- **GSM modem SMS** needs your own modem + SIM, and carrier compliance (A2P registration, local regulations) is on you.

## Using the adapters directly

```python
from caspian_adapters import Settings, build_providers

providers = build_providers(Settings(
    providers="instagram",
    instagram_page_id="<page id>",
    instagram_access_token="<page token>",
    instagram_app_secret="<app secret>",
    instagram_verify_token="<verify token>",
))
```

Each provider exposes `provision`, `send`, `reply`, `parse_webhook` (which verifies the platform's signature), and a `capabilities` set. See `.env.example` for every adapter's configuration and `packages/adapters/README.md` for the plugin entry-point contract.

## Development

```bash
uv sync
uv run pytest          # adapter + SDK test suites
uv run ruff check .
```

## License

Apache-2.0 for this repository (the `caspian-sdk` Python package is published under MIT).
