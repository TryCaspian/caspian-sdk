# caspian-opencode-plugin

OpenCode plugin that gives your agent a **Caspian email inbox** — humans email the agent, OpenCode runs, replies go back on the same thread.

**No Caspian account required.** On first start the plugin creates a sandbox project for you (same as `caspian init`).

## Install (anyone)

### Option A — project plugin (recommended while developing)

In your project:

```bash
mkdir -p .opencode/plugins
```

`.opencode/package.json`:

```json
{
  "dependencies": {
    "caspian-sdk": "^0.1.1",
    "caspian-opencode-plugin": "file:../caspian-sdk/packages/opencode"
  }
}
```

(From a sibling checkout of this monorepo; adjust the `file:` path to match.)

`.opencode/plugins/caspian.ts`:

```ts
export { default } from "caspian-opencode-plugin"
```

OpenCode installs `.opencode` deps with Bun at startup and loads the plugin automatically
([docs](https://opencode.ai/docs/plugins/)).

### Option B — npm package name in config

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["caspian-opencode-plugin"]
}
```

(Publish step TBD — until then use Option A / `file:`.)

### Option C — global plugin file

Copy or symlink into `~/.config/opencode/plugins/` and add deps in
`~/.config/opencode/package.json` the same way.

## First run (zero signup)

You do **not** need a Caspian account, CLI, or `.env`.

1. Start OpenCode with the plugin loaded.
2. If `CASPIAN_API_KEY` is missing, the plugin:
   1. Reuses env / project `.env` / `~/.config/opencode/caspian.env` if present
   2. If `caspian` is on PATH → `caspian init` (12s timeout)
   3. Else **HTTP mint** (no CLI): `POST https://api.trycaspianai.com/v1/projects/sandbox`
   4. Writes credentials to `<project>/.env` and `~/.config/opencode/caspian.env`
   5. Starts listen, then `connectEmail` in the background
3. Toast: **Caspian ready** → **Caspian inbox ready** with the agent address.

Cold start (no CLI / no `.env`) is typically ~1–3s to mint + inbox.

Optional CLI-only setup (same APIs):

```bash
pipx install caspian-cli   # or: uvx caspian-cli …
caspian init
caspian connect email
```

## Config (optional)

`~/.config/opencode/caspian.json`:

```json
{
  "displayName": "OpenCode Agent",
  "allowFrom": [],
  "channels": ["email"],
  "threading": {
    "enabled": true,
    "sharedSessionKey": "caspian:shared"
  },
  "switches": {
    "enabled": true,
    "autoOnboard": true,
    "autoConnectEmail": true,
    "degradedCapacityReply": true
  }
}
```

### Threading + session footer (TUI ↔ Gmail sync)

**Default: on.** Each Caspian conversation maps to an OpenCode session, **and**
outbound mail/replies stamp a footer:

```text
---
caspian-opencode:session=ses_0691…
```

That lets you:

1. Start in the TUI  
2. `/caspian:email friend@gmail.com I am alive` (footer uses current session id)  
3. Friend replies in Gmail (quote usually keeps the footer)  
4. Plugin routes the reply **back into the same TUI session**

| Setting | Default | Meaning |
|---|---|---|
| `threading.enabled` | `true` | One Caspian thread → one OpenCode session |
| `threading.sessionFooter` | `true` | Stamp/parse session footer for cross-channel sync |
| `threading.sharedSessionKey` | `caspian:shared` | Session key/title when threading is disabled |

Disable footer sync only if you explicitly want to:

```json
{
  "threading": { "sessionFooter": false }
}
```

Collapse **all** inbound mail into one session:

```json
{
  "threading": { "enabled": false }
}
```

| Switch | Default | Meaning |
|---|---|---|
| `autoOnboard` | `true` | Create sandbox project when no API key |
| `autoConnectEmail` | `true` | Provision email inbox on start |
| `enabled` | `true` | Global kill switch |

### Inbound notifications

When mail is delivered into an OpenCode session, the **server plugin** (no TUI
plugin) can alert you:

1. **OpenCode toast** — in-app banner (any OS where OpenCode runs)
2. **System notification**
   - macOS → Notification Center (`osascript`)
   - Linux → `notify-send` (install `libnotify` if missing)
   - Windows → PowerShell balloon tip (`NotifyIcon`)

Default (`~/.config/opencode/caspian.json`):

```json
{
  "notify": {
    "enabled": true,
    "toast": true,
    "system": true
  }
}
```

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch |
| `toast` | `true` | OpenCode TUI toast |
| `system` | `true` | Desktop / Notification Center |

### Thinking in channel replies

Model reasoning parts are **stripped** from Caspian replies by default (so Telegram /
email get only the final answer). OpenCode’s TUI may still show thinking locally.

```json
{
  "thinking": {
    "enabled": false,
    "channels": {
      "telegram": false,
      "email": false
    }
  }
}
```

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Global: include reasoning in channel replies |
| `channels.<name>` | _(unset)_ | Per-channel override (wins over `enabled`) |

Example: keep global off, but include thinking on email only:

```json
{ "thinking": { "enabled": false, "channels": { "email": true } } }
```

On macOS, allow notifications for **Script Editor** / **osascript** (or Terminal)
if the first alert is silent.

Env (also read from `.env` / `caspian.env`):

- `CASPIAN_API_KEY`
- `CASPIAN_BASE_URL` (default `https://api.trycaspianai.com`)

## Inbound vs outbound

| Direction | How |
|---|---|
| **Inbound** (default) | Someone emails your agent inbox → new/threaded OpenCode session → reply |
| **Outbound (new)** | `/caspian:email` → ask if needed → `caspian_send_email` → Caspian `initiate` |
| **Outbound (thread)** | In an inbound email session → `caspian_reply_email` → Caspian `reply` (same Gmail thread) |
| **Outbound (Telegram)** | `/caspian:telegram @user hi` → `caspian_send_telegram` |

Also: `/caspian:reply`, `/caspian-email`, tools `caspian_reply_email` / `caspian_send_email` / `caspian_send_telegram` / `caspian_inbox`.

**Telegram DM note:** bots cannot start a private chat. The recipient must message
your bot first before DMs work. The `/caspian:telegram` skill always surfaces this.

**Important:** if the session already has inbound mail, the agent must ask whether to
**reply on that thread** or **send a separate new email**. New emails require a real
subject (not bare `Re:`).

### Caspian Inbox skill (`caspian:Inbox`)

OpenCode skill **`caspian-inbox`** (alias **caspian:Inbox**) lists connections and
recent conversations/messages on configured channels (email by default; pass
other channels when connected).

| How | What |
|---|---|
| Skill | `skill({ name: "caspian-inbox" })` or ask “show Caspian inbox” |
| Slash | `/caspian:inbox` or `/caspian-inbox` |
| Tool | `caspian_inbox` (`list=true` default; optional `channels`, limits) |

Skill path: `.opencode/skills/caspian-inbox/SKILL.md`.

### Connect email / Telegram (skills)

Skills edit `~/.config/opencode/caspian.json` so admit includes the channel, then
**tell you to restart OpenCode** (config is loaded at plugin start).

| How | What |
|---|---|
| Slash | `/caspian:connect-email` · `/caspian:connect-telegram` · `/caspian:connect-discord` |
| Tools | `caspian_connections` · `caspian_connect_*` · `caspian_send_telegram` · `caspian_send_discord` |
| Skills | `caspian-connect-email` · `caspian-connect-telegram` · `caspian-connect-discord` · `caspian-discord` |

### Discord

| How | What |
|---|---|
| Connect | `/caspian:connect-discord` — one-click install (authorize URL) or `DISCORD_BOT_TOKEN` |
| Send | `/caspian:discord <channelId> <message>` — channel snowflake (`Copy Channel ID`) |
| Tool | `caspian_send_discord` |

After connect, restart OpenCode so `"channels"` includes `discord` (admit blast-radius).

Telegram token: put `TELEGRAM_BOT_TOKEN` in project `.env` or
`~/.config/opencode/caspian.env` — never in `caspian.json`. After connect you
should see `"channels": ["email", "telegram"]` (or your merged list). Until you
restart, Telegram is still rejected at admit.

### Which email identity?

Caspian can have **multiple** email connections on one API key. By default the plugin uses the first active email inbox (and won’t create a second if one exists).

Pin listen/send identity in `~/.config/opencode/caspian.json`:

```json
{
  "email": {
    "connectionId": "conn_fcce6ffe002befa41f33322a",
    "address": "example-bun-agent-2cac52@agents.trycaspianai.com",
    "listenConnectionIds": [],
    "listenAddresses": []
  }
}
```

| Field | Meaning |
|---|---|
| `email.connectionId` | Prefer this connection for send; also listen-filter if listen lists empty |
| `email.address` | Prefer this inbox address; also listen-filter if listen lists empty |
| `email.listenConnectionIds` | Only handle inbound for these connection ids |
| `email.listenAddresses` | Only handle inbound for these inbox addresses |

List identities: `uvx --from caspian-cli caspian status`

## How it works

```
Inbound:
  Human email → Caspian → listen() → OpenCode session.prompt → reply

Outbound:
  /caspian:email … → caspian_send_email tool → Caspian initiate(to, body)
```

Reliability model: [RELIABILITY.md](./RELIABILITY.md).

## Develop / test

```bash
cd packages/opencode
bun install
bun test          # unit + onboard + fault + capacity
bun run typecheck
```

| Suite | Proves |
|---|---|
| `onboard.test.ts` | CLI init, HTTP mint failover, `.env` persist |
| `pipeline.test.ts` | Email → prompt → reply critical path |
| `fault.test.ts` / `capacity.test.ts` | Fail-fast + isolation |

### Manual email check

1. Load plugin in OpenCode (Option A).
2. Note the logged inbox address.
3. `caspian test-email "ping"` (or send a real email).
4. Confirm OpenCode session activity + reply.

## License

Apache-2.0
