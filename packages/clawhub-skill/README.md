# Caspian skill for OpenClaw (ClawHub)

Gives an [OpenClaw](https://github.com/openclaw/openclaw) agent real communication
channels — Slack, Discord, Telegram, email, X, SMS — behind one `on_message`
handler, via [caspian-sdk](https://github.com/TryCaspian/caspian-sdk).

## Install

```bash
clawhub install @trycaspian/caspian
```

Free channels (email, Telegram, Slack, Discord) need no signup — the skill mints
a sandbox API key on first use and writes `CASPIAN_API_KEY` to `.env`.

## How it's built

The skill body is the live gateway doc at
[api.trycaspianai.com/SKILL.md](https://api.trycaspianai.com/SKILL.md) — the
source of truth for which channels are live. `frontmatter.md` holds the ClawHub
metadata. `publish.sh` stitches the two and publishes:

```bash
./publish.sh 1.0.2 --dry-run   # preview
./publish.sh 1.0.2             # publish (requires @trycaspian access)
```

Republish after any gateway doc change so the listing never drifts.

Looking for the native OpenClaw *plugin* (channel adapter, not skill)? See
[`packages/openclaw`](../openclaw).
