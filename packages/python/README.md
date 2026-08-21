# caspian-sdk

Give an agent an address on real messaging channels. One handler answers
Telegram, Slack, Discord, email, SMS, WhatsApp, X and more — hosted through the
Caspian gateway, or self-hosted straight against each platform with your own
tokens.

```bash
pip install caspian-sdk
```

Version 1.0.0 is a new API. It replaces the legacy `CommClient` (0.6.x); see
the migration note below.

## Hosted: the gateway owns credentials

```python
from caspian import Caspian

cx = Caspian(api_key="...")            # key from dashboard.trycaspianai.com
cx.channels.add("telegram", bot_token="...")   # telegram is BYO token

@cx.on_message({"overlap": "queue", "ack": "On it, one moment..."})
def handle(thread, msg, ctx):
    with thread.stream() as out:       # posts once, then edits as it writes
        out.append(answer(msg.text))

cx.run()                               # polls the gateway for inbound
```

## Self-host: your process, your tokens, no gateway

```python
cx = Caspian()
cx.channels.add("telegram", via="self-host", bot_token="...",
                webhook_url="https://your.server/telegram")

# from your own HTTP route:
results = cx.handle("telegram", request_body, request_headers)
```

Discord and Slack can receive over a held-open socket instead of a webhook —
no public URL needed:

```python
cx.channels.add("discord", via="self-host", bot_token="...")
cx.listen("discord")                   # pip install "caspian-sdk[discord]"
```

## What a handler can do

`thread.post`, `reply`, `send` (unthreaded), `send_media`, `send_blocks`,
`typing`, `edit`, `react`, `pin`, `delete`, `forward`, `initiate` (cold DM),
`schedule`, and `thread.stream()` for replies that type themselves out.
Rules take filters (`channel`, `kind`, `command`), an overlap policy
(`queue` / `debounce` / `drop` / `parallel`) and an instant `ack` for channels
with no typing indicator.

Your bot is data: `cx.app.rules` prints every predicate, policy and ack, so
programs are inspectable and testable without a network.

## Channels

telegram, slack, discord, email, sms, voice, whatsapp, messenger, imessage,
x and linear self-host adapters ship in the package. Hosted mode covers any
channel the gateway supports.

Optional extras: `caspian-sdk[discord]` and `caspian-sdk[slack-socket]`
(websocket inbound).

## Migrating from 0.6.x

The 0.6.x `CommClient` API is a different SDK. It remains published and its
source is tagged `legacy-sdk-0.6.x` in the repository. New code should start
here; there is no drop-in path between the two.

## Links

- Integration guide (written for coding agents): https://api.trycaspianai.com/SKILL.md
- Docs: https://www.trycaspianai.com/docs/
- Source and runnable examples for every channel:
  https://github.com/TryCaspian/caspian-sdk (`examples/`)
