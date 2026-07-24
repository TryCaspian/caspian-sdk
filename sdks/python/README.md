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

## Concurrency Policies

If a user sends multiple messages rapidly (a "burst") while your agent is still thinking, you can configure how `listen()` processes the overlap using the `concurrency` parameter:

```python
client.listen(concurrency="queue") # the default
```

- `"queue"` (default): Runs handlers sequentially per conversation. Ensures your agent doesn't double-reply or run concurrently for the same user.
- `"debounce"`: Waits briefly (`debounce_ms=500` by default) for a burst to settle. Your handler runs once with the latest message. Dropped messages are preserved in `message.coalesced_messages` if you need the full context.
- `"drop"`: If your agent is actively handling a message for a conversation, any new messages that arrive for that conversation are immediately dropped/ignored.
- `"parallel"`: Spawns a background thread for every message, letting them run entirely concurrently. Use with caution as this allows duplicate replies if the human sends overlapping messages.

## Docs

Point your coding agent at the setup guide and it does the whole integration for you. Full docs and your API key: **[trycaspianai.com](https://trycaspianai.com)**.

## License

MIT
