---
name: caspian-connect-email
description: >-
  Connect Caspian email for OpenCode: ensure credentials (sandbox or dashboard
  API key), write caspian.env, connect inbox, enable email in caspian.json.
  Use when the user wants to link email, set up the agent inbox, or runs
  /caspian:connect-email.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:ConnectEmail
  channel: caspian
---

# Caspian connect email

Get a Caspian API key (sandbox **or** existing dashboard key), save it to
`~/.config/opencode/caspian.env`, connect an email inbox, and enable `email` in
`caspian.json` channels.

## When to use

- User asks to connect / set up Caspian email
- User runs `/caspian:connect-email` or `/caspian-connect-email`
- First-time OpenCode + Caspian setup

## How

1. Call **`caspian_setup_credentials`** with `mode=status`.
2. If **not configured**, ask the user which path they want (do not invent keys):

   **A — Instant sandbox (recommended for trying Caspian)**  
   No account. Call `caspian_setup_credentials` with `mode=sandbox`.  
   Writes `CASPIAN_API_KEY` + `CASPIAN_BASE_URL=https://api.trycaspianai.com` to
   `~/.config/opencode/caspian.env` (and project `.env`).

   **B — Existing Caspian account**  
   Tell them to sign in at https://dashboard.trycaspianai.com/login , open a
   project, copy the API key, then call `caspian_setup_credentials` with
   `mode=paste` and `apiKey=<their key>`.

   If setup says **RESTART REQUIRED**, stop and tell them to quit + relaunch
   OpenCode, then run `/caspian:connect-email` again.

3. Call **`caspian_connections`** (optional) to show current links.
4. Call **`caspian_connect_email`** (optional `displayName`).
5. If the tool says **RESTART REQUIRED**, tell the user clearly:

   > Restart OpenCode (quit and relaunch) so the plugin reloads `caspian.json`
   > and admit includes `email`.

6. Do not invent connection ids or addresses. Report tool errors as-is.

## Notes

- Secrets go in `caspian.env` / `.env` — **never** in `caspian.json` or chat logs
  when you can avoid it (the tool masks keys in its output).
- CLI (`caspian init`) is optional; sandbox mint and paste cover the same APIs.
- After restart, inbound email should create OpenCode sessions automatically.
