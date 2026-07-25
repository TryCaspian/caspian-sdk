---
description: Send a Telegram message via Caspian (@username)
---

Load the **caspian-telegram** skill (alias caspian:Telegram), then call tool
`caspian_send_telegram` with `to` (@username or chat id) and `body`.

Always tell the user as a side note: Telegram bots cannot start a private chat —
the recipient must message the bot first before DMs work.

Do not claim success without a tool result.

Arguments:
$ARGUMENTS
