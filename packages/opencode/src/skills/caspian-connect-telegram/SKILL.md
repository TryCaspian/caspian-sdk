---
name: caspian-connect-telegram
description: >-
  Connect Caspian Telegram for OpenCode, enable telegram in caspian.json
  channels, and tell the user to restart. Use when linking a Telegram bot or
  running /caspian:connect-telegram.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:ConnectTelegram
  channel: caspian
---

# Caspian connect Telegram

Connect a Telegram bot to Caspian and enable `telegram` in the plugin admit
list (`channels` in `~/.config/opencode/caspian.json`). Without `telegram` in
`channels`, Telegram events are rejected at admit (blast-radius wall).

## When to use

- User asks to connect Telegram / BotFather / Telegram bot
- User runs `/caspian:connect-telegram` or `/caspian-connect-telegram`

## Prerequisites (token)

Bot token must exist in **one** of (prefer file over chat):

1. `TELEGRAM_BOT_TOKEN` in the environment
2. Project `.env` → `TELEGRAM_BOT_TOKEN=...`
3. `~/.config/opencode/caspian.env` → `TELEGRAM_BOT_TOKEN=...`

Do **not** store the token in `caspian.json`. Prefer not pasting the token into chat.

## How

1. Call **`caspian_connections`** to see if Telegram is already linked and whether
   `telegram` is already in plugin channels.
2. Call **`caspian_connect_telegram`**.
3. If the tool asks for a token, instruct the user to create a bot with
   @BotFather, write `TELEGRAM_BOT_TOKEN=...` into `.env` or `caspian.env`, then
   re-run the command. Do not invent a token.
4. On success, present connection id / channels. If **RESTART REQUIRED**, say:

   > Restart OpenCode (quit and relaunch) so admit loads
   > `"channels": ["email", "telegram"]` (or your updated list). Until then,
   > Telegram messages are still rejected.

5. After restart, Telegram inbound should flow into OpenCode like email.
