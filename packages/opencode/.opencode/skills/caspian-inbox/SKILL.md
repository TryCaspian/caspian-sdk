---
name: caspian-inbox
description: >-
  Caspian Inbox (caspian:Inbox) — list agent email and other configured channel
  conversations/messages. Use when the user asks for inbox, mail, unread email,
  Slack/Discord/channel messages via Caspian, or runs /caspian:inbox.
license: Apache-2.0
compatibility: opencode
metadata:
  alias: caspian:Inbox
  channel: caspian
---

# Caspian Inbox (`caspian:Inbox`)

List recent Caspian conversations and messages across **configured channels**
(default: email; also any other channels enabled in plugin config).

## When to use

- User asks “what’s in my inbox?”, “show emails”, “list Caspian messages”
- User runs `/caspian:inbox` or `/caspian-inbox`
- User mentions unread mail or channel threads on the agent inbox

## How

1. Call the tool **`caspian_inbox`** with `list` true (default).
2. Optionally pass:
   - `channels` — e.g. `email` or `email,slack`
   - `conversationLimit` — default 10
   - `messageLimit` — default 5
3. Present the tool output to the user (connections + conversations + recent messages).
4. Do **not** invent messages. If the tool errors, report the error.

## Address only

If the user only wants the agent email address, call `caspian_inbox` with
`list: false`.

## Related

- Send mail: tool `caspian_send_email` or `/caspian:email`
- Inbound mail is also pushed into OpenCode sessions automatically by the plugin
