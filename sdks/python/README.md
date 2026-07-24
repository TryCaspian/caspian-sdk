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

## Channels

| | Connect |
|---|---|
| **Email** | `connect_email()` — default domain or your own |
| **Slack** | `install_slack()` (one-click) or `connect_slack(...)` |
| **Discord** | `install_discord()` (one-click) or `connect_discord(...)` |
| **X / Twitter** | `install_x()` (one-click) or `connect_x(...)` |
| **WhatsApp** | `connect_whatsapp(...)` (Caspian hosted) |
| **SMS / phone** | `connect_phone(...)` — own GSM modem, or Caspian hosted |
| **Telegram** | `connect_telegram(bot_token=...)` |
| **iMessage** | `connect_imessage()` (Caspian hosted) |

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

## Concurrency and Overlapping Messages

When a user sends multiple messages in a row before your agent has finished replying, Caspian manages the overlap using a per-conversation concurrency strategy. You can set this via the `on_overlap` parameter in `listen()`.

```python
client.listen(on_overlap="queue")  # Default
```

### The Four Strategies

- **`queue` (Default)**: Queue overlapping messages and process them sequentially in FIFO order. *Use this as your safe default to guarantee your agent never double-replies or processes history out of order.*
- **`drop`**: Ignore new messages while a handler is already running for that conversation. *Use this when your agent's current task is expensive or side-effecting and shouldn't be interrupted — but be aware that any message arriving during that window is silently lost with no retry, whether it was important or not.*
- **`debounce`**: Wait `debounce_ms` after a message, discarding any earlier messages if a new one arrives in that window. *Use this when users tend to send multiple short messages in a row ("hi", "wait", "actually nevermind"), trading a small latency penalty for full context.*
  - **Tradeoff**: Your agent will *not* respond until the user goes quiet for the full `debounce_ms`. Earlier messages in the burst are discarded, not merged (e.g. if a user sends "my order number is 12345" then "it's broken" two seconds later, only "it's broken" reaches your handler — the order number is gone unless they repeat it).
  - *Note: When stopping the listener, `client.close()` makes a best-effort attempt to cancel pending debounce timers, but a timer that fires at the exact moment of shutdown may still execute.*
- **`parallel`**: Run all handlers concurrently. *Use this only if your agent is entirely stateless and idempotent, accepting the severe risk that out-of-order replies may severely confuse the user.*

LLM agents are inherently stateful (they build on previous conversation history). Processing overlapping messages sequentially (`queue`) prevents race conditions and ensures the agent always sees the correct chronological context.

## Docs

Point your coding agent at the setup guide and it does the whole integration for you. Full docs and your API key: **[trycaspianai.com](https://trycaspianai.com)**.

## License

MIT
