<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.svg">
    <img alt="Caspian — one identity for your AI agent, on every channel humans use" src="assets/banner-light.svg" width="720">
  </picture>
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

## Why Caspian exists

Teams that put agents on human channels drown in communication plumbing — and the pain isn't `send()`, it's **lifecycle and identity**: session/auth desync, reconnect loops, silent connection failures, cross-channel identity bugs. In our survey of 42 open-source agent projects, the largest frameworks each built **25+ internal channel adapters** and *still* spend **8–15% of their entire issue trackers** on channel plumbing.

Caspian's answer: **channels are transports, not identities.** The agent is one identity; every channel binds to it through the same small adapter interface, and your handler code never learns which platform it's on.

## Features

- **One handler, every channel** — `message.reply()` answers in the right thread on whatever platform the message arrived from.
- **Webhook verification as a hard boundary** — Slack signing secret, Meta `X-Hub-Signature-256`, Telegram secret header, X CRC, SES/SNS signatures. Mismatches are rejected, always.
- **Capability negotiation** — each adapter declares what its channel can physically do (send, reply, initiate, typing, group visibility); agents can never be granted more than the transport supports.
- **In-memory fakes for every channel** — the fakes consume each platform's *real* inbound payload shapes, so you test the full path offline. 70 tests, zero network.
- **Typing indicators & instant acks** — native "typing…" where the platform supports it (Discord, Telegram); `listen(ack="On it…")` for channels that don't.
- **Behavior guides** — `client.behavior_prompt()` returns per-channel etiquette (Slack threads, SMS length, X's 280 cap) to inject into your agent's system prompt.
- **Idempotent connects** — restart-safe: `connect_email()` returns the same inbox, never a duplicate.
- **Pluggable registry** — any provider package can register under the `caspian.providers` entry-point group. No forks.

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

Hosted channels are the same API — no numbers to buy, no platform review: **[trycaspianai.com](https://trycaspianai.com)**.

**\* The fine print** — read before you promise features:
- **X is not free**: DM send/receive needs a paid X API subscription on your X developer app (the free tier is write-only and capped).
- **Telegram user-account automation is ToS-gray**: it drives a personal account over MTProto and requires explicit opt-in config; bans are your risk. Never for spam.
- **GSM modem SMS**: your own modem + SIM; carrier compliance (A2P rules) is on you.

## Recipes

**Same agent, three channels:**

```python
client.connect_email(display_name="Acme Support")
client.connect_telegram(bot_token=BOT_TOKEN)
slack = client.install_slack(display_name="Acme Support")
print("Add to Slack:", slack["authorize_url"])   # one click, then it's live
# the @client.on_message handler you already wrote now answers on all three
```

**Platform-aware replies** — teach the agent each channel's etiquette in one line:

```python
system_prompt += "\n\n" + client.behavior_prompt()
```

**Multi-tenant** — one agent per customer, isolated by scope:

```python
acme = client.create_customer("Acme")
agent = client.create_agent("Support")
client.connect_slack(customer_id=acme["id"], agent_id=agent["id"], ...)
```

**Adapters without the SDK** — use the channel layer directly:

```python
from caspian_adapters import Settings, build_providers

providers = build_providers(Settings(
    providers="instagram",
    instagram_page_id="<page id>",
    instagram_access_token="<page token>",
    instagram_app_secret="<app secret>",
))
```

## What's in this repo

| Package | |
|---|---|
| [`packages/adapters`](./packages/adapters) | `caspian-adapters` — the channel adapters. One small interface per platform (`provision` / `send` / `reply` / `parse_webhook`), real signature verification, an offline fake per channel. |
| [`sdks/python`](./sdks/python) | `caspian-sdk` — the Python client: `on_message`, `connect_*()`, `message.reply()`, behavior guides. |
| [`apps/cli`](./apps/cli) | `comm` — init a project, connect channels, tail events from your terminal. |
| [`examples`](./examples) | Minimal runnable agents. |

## Roadmap

- **TypeScript SDK** — same `on_message` contract for the Node ecosystem
- **MCP server** — connect and message channels straight from any MCP-capable agent
- **More adapters** — the interface is small on purpose; [add one](./CONTRIBUTING.md#adding-a-new-channel-adapter)

## Community & support

- **Bugs / ideas** — [GitHub issues](https://github.com/TryCaspian/caspian-sdk/issues)
- **Security** — see [SECURITY.md](./SECURITY.md) (please, no public issues for vulnerabilities)
- **Hosted product & contact** — [trycaspianai.com](https://trycaspianai.com)

## Development

```bash
git clone https://github.com/TryCaspian/caspian-sdk.git
cd caspian-sdk && uv sync
uv run pytest        # 70 tests, all offline
uv run ruff check .
```

Contributions welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md).

**If Caspian saved you time, [a star](https://github.com/TryCaspian/caspian-sdk/stargazers) helps other agent builders find it.** ⭐

## License

Apache-2.0 for this repository. The `caspian-sdk` package on PyPI is MIT.
