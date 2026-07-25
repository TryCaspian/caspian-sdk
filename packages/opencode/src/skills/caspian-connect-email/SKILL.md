---
name: caspian-connect-email
description: >-
  Connect Caspian email for OpenCode and enable email in caspian.json channels.
  Use when the user wants to link email, set up the agent inbox, or runs
  /caspian:connect-email.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:ConnectEmail
  channel: caspian
---

# Caspian connect email

Connect (or reuse) a Caspian email inbox and ensure `channels` in
`~/.config/opencode/caspian.json` includes `email` so admit accepts inbound mail.

## When to use

- User asks to connect / set up Caspian email
- User runs `/caspian:connect-email` or `/caspian-connect-email`

## How

1. Call **`caspian_connections`** to show current Caspian connections and plugin channels.
2. Call **`caspian_connect_email`** (optional `displayName`).
3. Present the tool output. If it says **RESTART REQUIRED**, tell the user clearly:

   > Restart OpenCode (quit and relaunch) so the plugin reloads `caspian.json`
   > and admit includes `email`.

4. Do not invent connection ids or addresses. Report tool errors as-is.

## Notes

- Secrets stay in env / `.env` — never put API keys in chat or `caspian.json`.
- After restart, inbound email should create OpenCode sessions automatically.
