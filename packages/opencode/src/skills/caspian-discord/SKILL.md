---
name: caspian-discord
description: >-
  Post a Discord message via Caspian to a channel snowflake id. Use when the
  user runs /caspian:discord or asks to message a Discord channel through the bot.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:Discord
  channel: caspian
---

# Caspian Discord send (`caspian:Discord`)

Post a message to a Discord channel through the connected Caspian bot.

## When to use

- User runs `/caspian:discord` or `/caspian-discord`
- User asks to post / message a Discord channel via the agent

## How

1. Parse **to** (Discord channel snowflake id) and **body**.
2. Call tool **`caspian_send_discord`** with `to` and `body`.
3. Present the tool output. Always include:

   > **Discord:** `to` is a channel snowflake (Developer Mode → Copy Channel ID).
   > Invite the bot to the server first. For DMs, use the DM channel id after
   > the user has opened a DM with the bot.

4. Do not claim success without a tool result.
5. If Discord is not connected, suggest `/caspian:connect-discord` first.

## Reliability notes

- Outbound send is outside the inbound critical path; failures should not drop
  the listen loop.
- Inbound Discord only works when `discord` is in `caspian.json` `channels`
  (admit blast-radius) after restart.
