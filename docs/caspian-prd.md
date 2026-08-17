# Caspian PRD

**Status:** design  
**Companion:** `caspian-a-plus-b.md` (how it is built). This doc is **what ships and what it does**.

Caspian is the mouth and ears of an agent: one identity that can receive and send on the channels humans already use. It is not the brain (your model) and not the hands (GitHub, Gmail, browser tools).

Developers write a familiar bot API. Underneath, that becomes an inspectable list of rules so the same program can be tested, hosted, or self-hosted without rewriting it.

---

## 1. Who this is for

| Buyer | Job | What they use |
|---|---|---|
| **Bot developer** | Tutor, support, reminder bot on Discord + Telegram (+ maybe voice) | Core SDK + adapters + hosted or self-host |
| **AI builder** | Already on Agno, the AI SDK, or similar — wants the same agent loop on Telegram/Discord, not only in their app UI | Tool calling + inbound helper from the core SDK |
| **Coding / tool-calling agent** | Notify Slack, DM someone; inbound is optional | Tools + CLI; no message handlers required |

---

## 2. Goals

- One bot program across channels; add a channel without rewriting handlers.
- Hosted by default (we own identity + inbound). Self-host is an explicit opt-in.
- Full channel coverage in **adapters**, without growing a mega-language.
- Message handling never goes deaf: queue overlapping DMs; never queue a button ack behind a paragraph.
- Agents get a small tool list (`post_message`, …), not dozens of platform methods.

---

## 3. Features

Four product surfaces. Everything else is how those surfaces work.

### 3.1 Core SDK

The library bot developers write against: handlers, adapters, turn-taking, and a CLI for the same API.

**Friendly bot API.** `onMessage` / `onAction` / `thread.post`. Register handlers with options (`channel`, `kind: "dm"`, `overlap: "queue"`). This is the first README.

```ts
cx.onMessage({ channel: ["discord", "telegram"], overlap: "queue" }, async (thread, msg, { skipped }) => {
  await thread.typing();
  await thread.post(await tutor.run(msg, skipped));
});
```

**Hidden rule list.** Every public handler becomes a rule (when / how to overlap / what to do). Rules are data: “why didn’t it reply?” traces, Python and TypeScript stay in sync, hosted and self-host run the same program. Users do not import this.

**Channel adapters.** One driver per platform (`@caspian/telegram`, `@caspian/discord`, `@caspian/meet`, …). Turns a platform update into a plain message/button/reaction, and `thread.post` into the right send (formatting, keyboards). Full platform APIs live here (photos, topics, button-ack). The bot program never names `sendMessage`. Adding WhatsApp = a new adapter, not a new language. Button presses: the adapter always acknowledges so a spinner cannot hang because the author forgot.

**Overlap queue.** Policy named on the handler; machinery owned by the runner (our gateway when hosted, your Redis when self-host). Default for text: **queue**, then run the **latest** and pass `skipped` so the agent sees the burst. Bound (e.g. 16). Buttons/voice: **drop** (ack now). Telegram locks per chat; Slack per thread — the adapter picks the key. Ingress is separate: always persist and ACK the platform webhook first; that is not a handler option.

**Abstract post.** `thread.post(markdown, { actions: [button("Done", "done")] })` works on every channel that can render it. Adapters degrade where they must (SMS → numbered list). Need a photo or a forum topic? Call the adapter.

**Meet / voice.** `@caspian/meet` — transcripts in, speech out, overlap **drop** (barge-in). Same handlers as chat if you want; typically `onMessage({ channel: "meet", overlap: "drop" })`. Hosted `channels.add("meet")` returns a join URL.

**CLI (namespaced, same API as the SDK).** Thin client, one token, channel secrets stay on the gateway. Namespaces are **channel / resource / verb** — not a growing pile of top-level commands.

```bash
caspian login

caspian channels add telegram
caspian channels add discord --name Maya
caspian channels add telegram --via self-host --bot-token "$TG" \
  --webhook-url https://myapp.example.com/api/webhooks/telegram
caspian channels ls
caspian channels watch

caspian call post --thread telegram:123:456 --text "shipping now"
caspian telegram send-photo --thread telegram:123:456 --file ./graph.png
caspian slack post --thread slack:C123:ts --text "shipped"

caspian catalog
caspian catalog search "send a photo"
caspian catalog get telegram.send-photo

caspian threads ls --channel telegram
caspian threads tail telegram:123:456
caspian threads reply telegram:123:456 --text "on my way"

caspian run ./bot.ts --hosted
```

Coding agents prefer `caspian call post` (abstract) and `caspian telegram.*` only when they need a native method. MCP exposes the same namespaces: `catalog_search`, `catalog_get`, `call`, `channels`, `threads`.

---

### 3.2 Provisioning

How the bot gets an **identity** on a channel, and who receives inbound.

**Hosted is the default.** `cx.channels.add("telegram")` — Caspian owns the identity (or shared Discord bot / Slack app / Meet room) **and** the platform webhook. You may get an `authorizeUrl` to click.

**Self-host is opt-in.** `via: "self-host"` + your token + your `webhookUrl`. You own the bot and (usually) inbound. Omitting `via` never means “I forgot a token”; it means hosted. Self-host without the required secret is an error.

```ts
await cx.channels.add("discord", { displayName: "Maya" });
await cx.channels.add("telegram");
await cx.channels.add("telegram", {
  via: "self-host",
  botToken: process.env.TELEGRAM_BOT_TOKEN,
  webhookUrl: "https://myapp.example.com/api/webhooks/telegram",
});
```

**Webhooks — three different POSTs.**

| Kind | Who → whom | User-visible? |
|---|---|---|
| **Provisioning callback** | Discord/Slack → Caspian | No — flips connection to `active` |
| **Platform webhook** | Telegram → Caspian *or* your URL | Only if self-host |
| **Event delivery** | Caspian → your `/api/caspian` | Hosted: the only URL you expose |

Hosted: gateway registers the platform webhook at Caspian, ACKs in milliseconds, then POSTs a normalized event to you. Self-host: platform hits *your* route. One inbound owner per connection — never both. Send-only: `inbound: false` (no platform webhook).

---

### 3.3 Relationship memory

Who this person is, which conversation this is, what was said, whether the thread is subscribed, per-thread state.

**Does:** Lives with the runner (gateway when hosted, your store when self-host). Handlers ask (`thread.recent()`, `thread.state`) instead of holding process memory. A crash or a serverless invocation does not wipe the relationship. Cross-channel identity (same human on Telegram and Discord) builds on this store later — not a new API.

---

### 3.4 Tool calling (coding agents and other agent SDKs)

A small tool list over the same `post` / `edit` / `react` surface. Not a second product. Works for a coding agent that only sends, and for builders already on **Agno**, the **AI SDK**, or similar.

**What you get.** `cx.tools(thread, { preset: "messenger" })` — `post_message`, `edit_message`, `add_reaction`, `start_typing`, `send_dm`, …. The model never sees a raw platform chat id. Thread ids are `telegram:…` / `slack:…`. Optional native pack for power users. Coding agents use `preset: "outbound"` and never register `onMessage`.

**In Agno / AI SDK / others.** Drop the tool set into the framework you already use. Inbound is a helper that turns a request into messages that framework already understands, then `thread.post` for the reply.

| Mode | What happens |
|---|---|
| Hosted | Platform → Caspian → your `/api/caspian` → your agent loop → `thread.post` |
| Self-host | Platform → your `/api/webhooks/telegram` → same loop |
| Outbound only | Your agent calls tools; no inbound |

Mix per channel (hosted Discord + self-host Telegram). Your existing app UI stays yours; Caspian is the other transports.

CLI `caspian call …` is the same tools for a process that has no SDK.

---

## 4. Use cases

**AI tutor (Discord + Telegram + Meet)**  
Core SDK handlers + hosted provisioning ×3 → event URL `/api/caspian` → queue on text, drop on Meet → relationship memory for history → tools inside the tutor loop. CLI: `caspian channels watch`, `caspian threads tail`.

**AI builder on Agno or the AI SDK**  
Hosted: `add("telegram")` + inbound helper on `/api/caspian`. Self-host: `add(..., { via: "self-host", webhookUrl })` + webhook helper. Same tool set into `generateText` / Agno tools.

**Coding agent outbound**  
`add("slack")` or `add("telegram", { via: "self-host", inbound: false })`. No handlers. `caspian call post --thread slack:C…` or `cx.tools({ preset: "outbound" })`.

---

## 5. Acceptance: reminder bot + buttons

User: “remind me in 20 stretch” → bot replies with Snooze / Done → later sends the reminder unprompted → Snooze edits the message.

| Need | Feature |
|---|---|
| Identity + inbound | Provisioning (hosted or self-host) |
| Text DM → reply | Core SDK `onMessage` |
| Keyboard | Abstract `button()` → adapter |
| Button press must ack | Adapter law on `onAction` |
| Edit after Snooze | `thread.edit` / `edit_message` |
| Send later | Host timer + `thread.post` |
| Burst of “remind me” | Queue + `skipped` + relationship memory |
| Button during a reply | `onAction` uses `drop` |
| Photos / topics / reactions | Adapter methods |

If that flow cannot be written with `onMessage` + `onAction` + `thread.post` / `edit` (plus a timer in host code), adapters are not done.

---

## 6. Success

- Tutor example runs hosted from one program.
- Reminder + Snooze/Done works without the author acking the button themselves.
- Hosted AI builder: platform never hits their app; they only see `/api/caspian`.
- Self-host AI builder: no Caspian key required; platform hits their webhook route.
- `caspian call post --thread …` sends as the hosted identity with no channel token in the shell.
- `caspian catalog search photo` lists `telegram.send-photo` from the adapter.
- Adding a channel adds an adapter package + CLI namespace, not new handler types.
