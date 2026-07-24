# caspian-sdk

**Give your AI agent one identity that reaches any human, on whatever app they already use** — email, Slack, Discord, WhatsApp, SMS, X, Telegram, iMessage — all behind a single `on_message` handler.

You write the handler once. Caspian handles the provider quirks, threading, delivery, and dedup for every channel.

```bash
pip install caspian-sdk
```

## Quickstart

```python
from caspian_sdk import CommClient

client = CommClient(api_key="YOUR_KEY")

# Connect any channel — email needs nothing; others take a token or one-click OAuth.
inbox = client.connect_email()
print("Agent address:", inbox["address"])

@client.on_message
def handle(message):
    # The same handler answers every channel you connect.
    message.reply(f"Thanks! You said: {message.text}")

client.listen()  # one loop, every channel
```

`api_key` and `base_url` fall back to `CASPIAN_API_KEY` / `CASPIAN_BASE_URL` from the
environment or a local `.env`, so `CommClient()` with no arguments works too. The legacy
`COMM_API_KEY` / `COMM_BASE_URL` names are still honoured as a fallback.

## Channels

| | Connect |
|---|---|
| **Email** | `connect_email()` — default domain or your own |
| **Slack** | `install_slack()` (one-click) or `connect_slack(...)` |
| **Discord** | `install_discord()` (one-click) or `connect_discord(...)` |
| **GitHub issues / PRs** | `install_github()` or `connect_github(...)` |
| **X / Twitter** | `install_x()` (one-click) or `connect_x(...)` |
| **WhatsApp** | `connect_whatsapp(...)` (Caspian hosted) |
| **SMS / phone** | `connect_phone(...)` — own GSM modem, or Caspian hosted |
| **Telegram** | `connect_telegram(bot_token=...)` |
| **iMessage** | `connect_imessage()` (Caspian hosted) |

## Make your agent platform-aware

Each channel behaves differently (Slack threads, WhatsApp's 24-hour window, SMS
length, iMessage has no markdown). Pull per-channel etiquette for the channels
you've connected and drop it into your agent's system prompt:

```python
guide = client.behavior_prompt()
system_prompt += "\n\n" + guide
# or one channel: client.channel_guide("slack")
```

Use it, tweak it, or ignore it and write your own.

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
- **`client.listen()`** is resilient — a handler error or a dropped poll won't stop the loop; only `Ctrl+C` (KeyboardInterrupt) stops it. Pass `ack=` to auto-send an instant acknowledgement the moment a message arrives, before your handler runs — handy on channels with no typing indicator (X, SMS, email):

```python
client.listen(ack="On it — one moment…")
```

## Errors

Non-2xx responses raise a `CommError` with `status_code` and `detail`. Two paid-channel
cases raise typed subclasses that carry structured fields, so you can react in code:

```python
from caspian_sdk import AccountRequiredError, CommError, InsufficientCreditError

try:
    message.reply("On it!")
except AccountRequiredError as err:
    # 401 — a paid channel needs a one-time developer sign-in first.
    err.login()  # runs the device sign-in; or read err.login_options
except InsufficientCreditError as err:
    # 402 (out of credit) or 429 (spend cap reached).
    print(f"Balance: {err.balance_cents}¢")
    err.top_up(2000)  # mint a Stripe checkout link to refill; or read err.payment_options
except CommError as err:
    # Anything else — e.g. a 422 validation error.
    print(f"{err.status_code}: {err.detail}")
```

- **`AccountRequiredError`** (HTTP 401) — `reason`, `message`, `login_options`; `.login()` runs the sign-in.
- **`InsufficientCreditError`** (HTTP 402 / 429) — `reason`, `balance_cents`, `payment_options`; `.top_up()` mints a refill link.
- **`CommError`** — base class for every other non-2xx response.

## Overlapping messages

`listen()` uses a separate queue for each conversation, so a slow reply in one
conversation does not block everyone else. The default is `queue`:

```python
client.listen(concurrency="queue")
```

Choose a different policy when the handler does not need every message:

| Policy | Behavior | Use when |
|---|---|---|
| `queue` | Run every message in order for that conversation | The agent must handle every message |
| `debounce` | Wait for a pause, then run only the latest message | Several quick messages should become one turn |
| `drop` | Ignore new messages while that conversation is busy | Skipping interruptions is acceptable |
| `parallel` | Run every message immediately | Handlers are independent; replies may finish out of order |

Set the debounce window in milliseconds:

```python
client.listen(concurrency="debounce", debounce_ms=500)
```

The queues live in the client process. Multiple agent processes need their own
shared coordination layer.

## Docs

Point your coding agent at the setup guide and it does the whole integration for you. Full docs and your API key: **[trycaspianai.com](https://trycaspianai.com)**.

## License

MIT
