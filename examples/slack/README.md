# Slack bot (Python)

Use Socket Mode so there is no public URL. Slack Events `url_verification`
currently parses to `[]` with no HTTP echo — this example does **not** use
`handle("slack")` or `serve()`. Catalog socket inbound is
`cx.listen("slack")`.

Handlers live in `app.py`. `bot.py` only adds the channel and holds the socket.

## Install

The socket extra pulls in `websockets` (see `packages/python/pyproject.toml`):

```bash
cd packages/python
uv sync --extra slack-socket
```

Or: `pip install 'caspian-sdk[slack-socket]'`.

## Socket Mode

In the [Slack API](https://api.slack.com/apps) app settings:

1. Enable **Socket Mode**.
2. Create an **App-Level Token** (`xapp-`) with `connections:write`.
3. Install the app to a workspace and copy the **Bot User OAuth Token** (`xoxb-`).
4. Copy **Signing Secret** from Basic Information (kept on the connection even
   though Socket Mode inbound is trusted).

Invite the bot to a channel. Subscribe to the events you need (at least
`message.channels` / `message.groups` / `message.im` as appropriate).

## Run

```bash
export SLACK_BOT_TOKEN='xoxb-…'
export SLACK_APP_TOKEN='xapp-…'
export SLACK_SIGNING_SECRET='…'

cd packages/python
uv run python ../../examples/slack/bot.py
```

Send `/help` in a channel. Ctrl+C stops the process.
