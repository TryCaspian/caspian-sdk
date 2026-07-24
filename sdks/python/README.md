# caspian-sdk

**Give your AI agent one identity that reaches any human, on whatever app they already use** — email, Slack, Discord, WhatsApp, SMS, X, Telegram, iMessage — all behind a single `on_message` handler.

You write the handler once. Caspian handles the provider quirks, threading, delivery, and dedup for every channel.

```bash
pip install caspian-sdk
```

## Quickstart

```python
from caspian_sdk import CommClient

client = CommClient(api_key="...")

# Connect any channel — email needs nothing; others take a token or one-click OAuth.
inbox = client.connect_email()
print("Agent address:", inbox["address"])

@client.on_message
def handle(message):
    # The same handler answers every channel you connect.
    message.reply(f"Thanks! You said: {message.text}")

client.listen()  # one loop, every channel
```

`api_key` and `base_url` fall back to `CASPIAN_API_KEY` / `CASPIAN_BASE_URL` from the environment or a local `.env`, so `CommClient()` with no arguments works too.

## Channels

|                 | Connect                                                   |
| --------------- | --------------------------------------------------------- |
| **Email**       | `connect_email()` — default domain or your own            |
| **Slack**       | `install_slack()` (one-click) or `connect_slack(...)`     |
| **Discord**     | `install_discord()` (one-click) or `connect_discord(...)` |
| **X / Twitter** | `install_x()` (one-click) or `connect_x(...)`             |
| **WhatsApp**    | `connect_whatsapp(...)` (Caspian hosted)                  |
| **SMS / phone** | `connect_phone(...)` — own GSM modem, or Caspian hosted   |
| **Telegram**    | `connect_telegram(bot_token=...)`                         |
| **iMessage**    | `connect_imessage()` (Caspian hosted)                     |

## Make your agent platform-aware

Each channel behaves differently - Slack has threads, WhatsApp has a 24-hour
messaging window, SMS has length limits. Pull per channel etiquette for the
channels you've connected and drop it into your system prompt:

```python

system_prompt += "\n\n" + client.behavior_prompt()

# for one channel

guide = client.channel_guide("slack")

```

## Rich messages

Send one provider-neutral `blocks` payload and each channel gets its best
rendering — Slack, Discord and Telegram render natively, email gets rich HTML,
and text-only channels degrade to clean text automatically.

```python
from caspian_sdk import blocks as b

message.reply(blocks=[
    b.card(
        title="Order #1024 shipped",
        subtitle="Arriving Thursday",
        buttons=[
            {"label": "Track", "url": "https://example.com/track/1024"},
            {"label": "Get help", "value": "help:1024"},  # callback
        ],
    ),
])
```

Block types: `heading`, `text`, `divider`, `image`, `fields`, `list`, `buttons`,
`card`. A button with a `url` is a link; a button with a `value` is a callback.

## How it works

- **One handler, every channel.** Adding a channel is another `connect_*()` call — never new handler code.
- **`message.reply()`** answers in the right thread on the right channel automatically.
- **`message.typing()`** shows a "typing…" indicator while your agent thinks (where the platform supports it).
- **`client.listen()`** is resilient — a handler error or a dropped poll won't stop the loop.
  Pass `ack="On it…"` to send an instant reply the moment a message arrives, before your handler
  runs; this is useful for channels with no typing indicator.

## Errors

Non-2xx responses raise `CommError` with `.status_code` and `.detail`.
Two typed subclasses carry extra fields for paid channels:

```python
from caspian_sdk import CommClient, CommError, AccountRequiredError, InsufficientCreditError

client = CommClient()

try:
    client.connect_whatsapp(...)
except AccountRequiredError as e:
    # paid channel needs a one-time developer sign-in first
    e.login()
except InsufficientCreditError as e:
    # HTTP 402/429 — out of credit or spend cap reached
    print(f"Balance: {e.balance_cents} cents")
    url = e.top_up()["checkout_url"]  # Stripe link to add credit
    print("Add credit at:", url)
except CommError as e:
    # fallback for all other non-2xx responses
    print(e.status_code, e.detail)
```

## Docs

Point your coding agent at the setup guide and it does the whole integration for you. Full docs and your API key: **[trycaspianai.com](https://trycaspianai.com)**.

## License

MIT
