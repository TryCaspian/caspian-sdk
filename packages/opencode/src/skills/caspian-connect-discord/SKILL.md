---
name: caspian-connect-discord
description: >-
  Connect Caspian Discord for OpenCode (install OAuth or BYO bot token), enable
  discord in caspian.json channels, and tell the user to restart. Use when
  linking Discord or running /caspian:connect-discord.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:ConnectDiscord
  channel: caspian
---

# Caspian connect Discord

Connect Discord to Caspian and enable `discord` in the plugin admit list
(`channels` in `~/.config/opencode/caspian.json`). Without `discord` in
`channels`, Discord events are rejected at admit (blast-radius wall).

Connect is **non-CP** (failures must not kill the email/telegram listen loop).
Admit reload requires **restart OpenCode**.

## When to use

- User asks to connect Discord
- User runs `/caspian:connect-discord` or `/caspian-connect-discord`

## How

1. Call **`caspian_connections`**.
2. Call **`caspian_connect_discord`**.
   - Default: one-click `installDiscord()` if no `DISCORD_BOT_TOKEN`
   - Or BYO: `DISCORD_BOT_TOKEN` in `.env` / `caspian.env`
   - `preferInstall: true` forces OAuth install even with a token
3. If an **Authorize URL** is returned, tell the user to open it and invite
   the bot to their server. Wait until connection status is `active`.
4. If **RESTART REQUIRED**, say clearly:

   > Restart OpenCode so admit loads `discord` in `channels`. Until then,
   > Discord messages are rejected at the blast-radius wall.

5. Never store the bot token in `caspian.json`.
