# Discord bot (Python)

Guild messages have no HTTP webhook in this SDK. Catalog inbound is **socket
only** — `cx.listen("discord")`, not `serve()` or `handle("discord")`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds the socket.

## Install

The socket extra pulls in `websockets` (see `packages/python/pyproject.toml`):

```bash
cd packages/python
uv sync --extra discord
```

Or: `pip install 'caspian[discord]'`.

## Message Content Intent

In the [Discord Developer Portal](https://discord.com/developers/applications),
open the bot → **Privileged Gateway Intents** → enable **Message Content
Intent**. Without it, guild messages arrive with empty text and `/help` never
matches.

Invite the bot with a scope that includes `bot` and the permissions you need
(send messages, add reactions, pin, etc.).

## Run

```bash
export DISCORD_BOT_TOKEN='…'

cd packages/python
uv run python ../../examples/discord/bot.py
```

Send `/help` in a guild channel. Ctrl+C stops the process.
