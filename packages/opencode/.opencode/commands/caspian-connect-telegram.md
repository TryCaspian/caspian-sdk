---
description: Connect Caspian Telegram and enable telegram in caspian.json
---

Load the **caspian-connect-telegram** skill, then:

1. Call tool `caspian_connections`
2. Call tool `caspian_connect_telegram`
3. If missing token, tell the user to set TELEGRAM_BOT_TOKEN in .env or
   ~/.config/opencode/caspian.env (not caspian.json), then retry
4. If output says RESTART REQUIRED, tell the user to quit and relaunch OpenCode
   so admit includes telegram

Arguments:
$ARGUMENTS
