<h1 align="center">Caspian</h1>

<p align="center">
  <strong>One identity for your AI agent on every channel humans use — behind a single <code>on_message</code> handler.</strong>
</p>

<p align="center">
  <a href="https://trycaspianai.com">Website</a>
  ·
  <a href="https://pypi.org/project/caspian-sdk/">PyPI</a>
  ·
  <a href="./llms.txt">llms.txt for agents</a>
  ·
  <a href="./CONTRIBUTING.md">Contributing</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/caspian-sdk/"><img alt="PyPI" src="https://img.shields.io/pypi/v/caspian-sdk?color=%2334D058&label=caspian-sdk" /></a>
  <a href="https://pepy.tech/project/caspian-sdk"><img alt="Downloads" src="https://img.shields.io/pypi/dm/caspian-sdk" /></a>
  <a href="https://pypi.org/project/caspian-sdk/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/caspian-sdk" /></a>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue" /></a>
  <a href="https://github.com/TryCaspian/caspian-sdk"><img alt="GitHub stars" src="https://img.shields.io/github/stars/TryCaspian/caspian-sdk?style=social" /></a>
</p>

---

Your agent's reasoning decides **what** to say. Caspian is **how it exists** on Slack, Discord, Telegram, Instagram, email, X, and beyond — one connect call per channel, one handler for all of them, threading and webhook verification handled.

```bash
pip install caspian-sdk
```

```python
from caspian_sdk import CommClient

client = CommClient()  # reads COMM_API_KEY / COMM_BASE_URL from .env
email = client.connect_email(display_name="My Agent")
print("Agent email:", email["address"])

@client.on_message
def handle(message):
    message.reply(f"You said: {message.text}")

client.listen()  # one loop, every channel
```

Adding a channel is one more `connect_*()` call — never new handler code.

> **Using a coding agent?** Point it at [`llms.txt`](./llms.txt) — or, against a running gateway, `GET /SKILL.md` — and it can do the entire integration for you.

## Channels

| Channel | This repo (your credentials) | Caspian hosted |
|---|:---:|:---:|
| Email (AWS SES) | ✅ | ✅ instant inbox |
| Telegram (bot) | ✅ | ✅ |
| Discord | ✅ | ✅ one-click |
| Slack | ✅ | ✅ one-click |
| Instagram DM | ✅ | ✅ |
| Facebook Messenger | ✅ | ✅ |
| X / Twitter | ✅ * | ✅ |
| Google Meet | ✅ | ✅ |
| SMS (GSM modem) | ✅ * | ✅ no hardware |
| Telegram (user account) | ⚠️ opt-in * | — |
| WhatsApp Business | — | ✅ one-click |
| Phone / voice · iMessage · RCS | — | ✅ |

Hosted channels are the same API — no numbers to buy, no platform review: **[trycaspianai.com](https://trycaspianai.com)**. Any provider package can plug into the same registry via the `caspian.providers` entry-point group.

**\* The fine print** — read before you promise features:
- **X is not free**: DM send/receive needs a paid X API subscription on your X developer app (the free tier is write-only and capped).
- **Telegram user-account automation is ToS-gray**: it drives a personal account over MTProto and requires explicit opt-in config; bans are your risk. Never for spam.
- **GSM modem SMS**: your own modem + SIM; carrier compliance (A2P rules) is on you.

## What's in this repo

| Package | | 
|---|---|
| [`packages/adapters`](./packages/adapters) | `caspian-adapters` — the channel adapters. One small interface per platform (`provision` / `send` / `reply` / `parse_webhook` + capability negotiation), real webhook signature verification, and an in-memory fake per channel for offline tests. |
| [`sdks/python`](./sdks/python) | `caspian-sdk` — the Python client: `on_message`, `connect_*()`, `message.reply()`, per-channel behavior guides. |
| [`apps/cli`](./apps/cli) | `comm` — init a project, connect channels, tail events from your terminal. |
| [`examples`](./examples) | Minimal runnable agents. |

## Using the adapters directly

```python
from caspian_adapters import Settings, build_providers

providers = build_providers(Settings(
    providers="instagram",
    instagram_page_id="<page id>",
    instagram_access_token="<page token>",
    instagram_app_secret="<app secret>",
))
```

Every adapter speaks its platform's **official API** and verifies inbound webhooks (Slack signing secret, Meta `X-Hub-Signature-256`, Telegram secret header, X CRC, SES/SNS signatures). See [`.env.example`](./.env.example) for every knob.

## Development

```bash
git clone https://github.com/TryCaspian/caspian-sdk.git
cd caspian-sdk && uv sync
uv run pytest        # 70 tests, all offline
uv run ruff check .
```

Contributions welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md). Security reports: [SECURITY.md](./SECURITY.md).

## License

Apache-2.0 for this repository. The `caspian-sdk` package on PyPI is MIT.
