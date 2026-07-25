---
name: caspian-telegram
description: >-
  Send a Telegram message via Caspian to a @username (or chat id). Use when the
  user runs /caspian:telegram or asks to DM someone on Telegram through the bot.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:Telegram
  channel: caspian
---

# Caspian Telegram send (`caspian:Telegram`)

Send a message to a Telegram user through the connected Caspian bot.

## When to use

- User runs `/caspian:telegram` or `/caspian-telegram`
- User asks to DM / message someone on Telegram via the agent bot

## How

1. Parse **to** (`@username` or numeric chat id) and **body** from the user.
2. Call tool **`caspian_send_telegram`** with `to` and `body`.
3. Present the tool output. Always include this side note (even on success):

   > **Telegram:** bots cannot start a private chat. The recipient must message
   > your bot first (open the bot → Start / send any message) before DMs work.

4. Do not claim success without a tool result. If the tool fails because there
   is no prior conversation, explain they need to Start the bot first.

## Do not

- Use `caspian_send_email` for Telegram
- Invent usernames or pretend the DM arrived without a tool result
