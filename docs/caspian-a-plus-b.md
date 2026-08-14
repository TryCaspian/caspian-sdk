# Caspian: A-core + B-surface

Bot developers write a Chat SDK–shaped API (B). That API **desugars** into a
small kernel algebra (A). Interpreters run A. Developers never have to import A.

Adapters are how one channel (Telegram, Discord, Meet, …) speaks Events and
Commands. They are not the language.

This is the design we are building toward. It is not the current `CommClient`.
Feature list (what ships): `caspian-prd.md`.

---

## 0. Why, in plain words

**Why a DSL-like core inside (`/core`), when nobody asked for a language?**

Because a bot is a promise: "when X happens, do Y, and never talk over
yourself." If that promise only exists as code inside async callbacks, nobody
— not us, not tests, not the gateway — can *read* it. It can only be run live
and observed.

Keeping the promise as a **value** (a list of rules) means:

- **We can test it without Telegram.** Feed a fake message in, look at what
  the bot *would* do. No token, no ngrok, no flaky CI.
- **We can explain it.** "Why did the bot not reply?" becomes a trace you can
  print, not a debugging session.
- **We can set up channels correctly by reading it.** If the rules never
  handle button presses, don't ask Telegram to send button events. The
  program *is* the configuration.
- **Python and TypeScript can't drift.** Both surfaces produce the same rule
  list; one golden test suite checks both.
- **Hosted and self-hosted stay honest.** Same rules run in our cloud, on
  your laptop, or in a test — only the surroundings change.

None of that works if the "program" is a pile of closures. That's the whole
argument. It's not elegance; it's that a bot's behavior should be inspectable
data, the same way a config file is — without making developers write config
files.

**Why expose it Chat SDK-style then?**

Because nobody wants to learn an algebra to reply to a DM. `cx.onMessage(fn)`
is what every bot developer already knows. So the friendly API is the *only*
public one, and it quietly builds the rule list underneath. Developers get
Express-like ergonomics; we get an inspectable program. Power users can drop
to the core when the options object runs out — everyone else never sees it.

**Where the queue lives.** When three messages arrive while the bot is still
answering the first, something must decide: wait, collapse, or ignore. That
decision is *named* in the rule ("queue" / "debounce" / "drop") but *done* by
whoever runs the program — our gateway in hosted mode, your Redis in
self-hosted, plain memory in tests. You say the policy; the runner owns the
machinery. Per conversation, bounded, and button-presses never wait behind
paragraphs.

**Where relationship memory lives.** Who this person is, which conversation
this is, what the bot said before, "this thread is subscribed" — that's
state, and state lives with the runner too (gateway in hosted, your store in
self-hosted). Handlers ask for it (`thread.recent()`, `thread.state`) instead
of holding it, which is why a crashed or serverless process loses nothing.

**Where adapters fit.** One driver per channel. Telegram's driver turns a
Telegram update into a plain "message" event and turns the bot's "post this"
into the right Bot API call — including channel chores like acknowledging a
button press so the spinner stops. Rules never mention Telegram; drivers
never make decisions. Adding a channel = adding a driver, not rewriting the
bot.

**Where provisioning fits.** Getting the bot an *identity* on a channel is
paperwork, not behavior. **Default is hosted** — `channels.add("telegram")`
means Caspian owns the identity and the inbound webhooks. Opt in to
`via: "self-host"` only when you bring your own bot token (and usually your
own webhook URL). The rules never know which recipe ran; swap hosted for
self-host and no rule changes.

One sentence: **rules as data in the middle; a familiar API on top; drivers
at the edges; the runner holds the queues and the memory; provisioning is
paperwork on the side.**

---

## 1. Two layers, one program

```text
B  (what you type)     cx.onMessage({ channel: "telegram", kind: "dm" }, fn)
        │ desugar
        ▼
A  (the program)       App([ Rule(pred, overlap, [Typing(), Host(fn)]) ])
        │ interpret
        ▼
world                  Memory | process (your webhook) | hosted gateway
```

**Rule:** every public B API must have an A constructor and a conformance
vector. If B grows an `onTyping` that is only a closure, A is dead.

A is Python-operator-friendly (`message & ~dm()`). TypeScript uses methods
(`.and()` / `.not()`) — `&` / `~` are bitwise there.

---

## 2. What an adapter is

An **adapter** is a channel pack: the only code that knows Telegram’s Bot API,
Discord’s gateway, Meet’s media plane, etc.

It is **not**:

- the `App` / `on` / `message` language (that’s A)
- the fluent `Caspian` / `onMessage` facade (that’s B)
- OAuth, `channels.add`, API keys (that’s provision)
- overlap queues, outbox, billing (that’s the interpreter / hosted runtime)

It **is** a translation layer with a fixed job:

```text
platform bytes  ──parse──▶  Event          (into A)
Command         ──execute─▶ platform HTTP  (out of A)
thread id  ◀──encode/decode──▶  telegram:-100123:42
overlap key  ──▶  chat_id (Telegram) vs channel:ts (Slack)
```

Same idea as Vercel Chat SDK’s `Adapter` interface (`handleWebhook`,
`postMessage`, `parseMessage`, `encodeThreadId`, optional `openModal?`, …).

### The interface (A’s port)

```python
class Adapter(Protocol):
    name: str                          # "telegram"

    def parse(self, request: RawInbound) -> list[Event]:
        """Webhook/poll payload → kernel Events. Unknown update types: []."""

    def execute(self, cmd: Command, conn: Connection) -> Result[Sent, Err]:
        """Post/Edit/Typing/React/... → Bot API. Unknown Command: Err."""

    def encode_thread(self, native: NativeThread) -> str:
        ...

    def decode_thread(self, thread_id: str) -> NativeThread:
        ...

    def overlap_key(self, event: Event) -> str:
        """Telegram: chat_id. Slack: channel:thread_ts."""

    def capabilities(self) -> frozenset[str]:
        """receive, reply, send, buttons, voice, ..."""

    def format(self) -> FormatConverter:
        """markdown/AST ↔ platform text (MarkdownV2, mrkdwn, ...)."""
```

Optional extras (`answer_callback`, `send_photo`, `open_modal`) live **on the
adapter object**, not in A. Core only knows `Post`, `Edit`, `React`, `Typing`,
`Subscribe`, `SetState`, `Call`, `Host`. If Telegram must `answerCallbackQuery`,
the adapter does that when executing an `Action` turn — App authors don’t.

### Construction (B)

```ts
import { Caspian } from "caspian";
import { telegram } from "@caspian/telegram";
import { discord } from "@caspian/discord";

const cx = new Caspian({
  adapters: {
    telegram: telegram({ token: process.env.TELEGRAM_BOT_TOKEN }), // BYO
    discord: discord(),                                            // creds from provision
  },
  hosted: { apiKey: process.env.CASPIAN_API_KEY }, // optional executor
});
```

`telegram()` returns an `Adapter`. The kernel never imports `@caspian/telegram`.

### Three executors, one adapter

The adapter’s *methods* stay the same. Who **calls** `execute` changes:

| Executor | `parse` | `execute` talks to |
|---|---|---|
| **Memory** | fixture bytes | nothing (Commands recorded) |
| **Process** | your Next.js `POST` | `api.telegram.org` with BYO token |
| **Hosted** | Caspian’s webhook | Caspian outbox (secrets stay on the gateway) |

Hosted is not a second Telegram adapter. It is `interpret(app, adapter, executor=hosted)`.

### Full coverage vs abstraction

- **L2 / A:** `Post("hi", actions=[button("ok")])` — works on every adapter that
  can render buttons; others degrade (SMS → numbered list / drop).
- **L3 / adapter:** `telegram.answerCallback(...)`, `sendPhoto` — typed methods
  on the pack. App can `Call("telegram.send_photo", args)` or the host fn uses
  `thread.native` / pack helpers.
- **Raw:** `event.raw` is the platform Update. Escape hatch, not the default.

---

## 3. B surface (what bot developers write)

```ts
cx.onMessage(
  { channel: "telegram", kind: "dm", overlap: "queue" },
  async (thread, msg, { skipped }) => {
    await thread.typing();
    await thread.post(await tutor.run(msg, skipped));
  },
);

cx.onAction({ overlap: "drop" }, async (thread, act) => {
  await thread.post("ok");
});
```

Desugars to A `Rule`s. `thread.post` enqueues a `Post` Command for this turn.

Power-user escape (A, opt-in; Python shown because operators work):

```python
from caspian.core import on, message, channel, dm, queue

cx.use(on(message & channel("telegram") & ~dm(), overlap=queue(), then=handler))
# ~dm() = not a DM = group/channel. TS: dm().not()
```

---

## 4. Provision (not an adapter, not A)

Default is **hosted**. Omit `via` and Caspian mints/attaches a Caspian-owned
identity (shared Discord bot, hosted Telegram bot / start link, Slack OAuth
through *our* app, Meet room). `via: "self-host"` is the opt-in: you bring
the token (and typically the webhook URL). There is no `via: "credentials"`
and no `via: "oauth"` — OAuth is how *hosted* Slack/Discord finish becoming
`active`, not a recipe the caller names.

```ts
await cx.channels.add("discord", { displayName: "Maya" });     // hosted
await cx.channels.add("telegram");                            // hosted
await cx.channels.add("slack");                               // hosted (OAuth click)
await cx.channels.add("meet");                                // hosted room

await cx.channels.add("telegram", {                           // opt-in BYO
  via: "self-host",
  botToken: token,
  webhookUrl: "https://myapp.vercel.app/api/webhooks/telegram",
});
```

Omitting `via` never means “I forgot a token.” It means hosted. Self-host
without `botToken` (or the channel’s required secret) is an error. Yields a
`Connection` the adapter/executor close over. Core does not import this module.

---

## 5. Package shape

```text
caspian              B facade + A (private) + MemoryInterpreter
@caspian/telegram    Adapter
@caspian/discord     Adapter
@caspian/ai          optional: AI SDK tools + handleWebhook → same runtime inbound
```

Hosted = `{ hosted: { apiKey } }` on the constructor, not a package.
Internal modules `core/` and `provision/` are import-linted (`core` ⊬ `provision`).

Tool-calling agents never write `onMessage`. They use `cx.tools({ preset: "outbound" })`
derived from A’s Command types, plus adapters for send.

---

## 6. Webhooks

Webhooks are **runtime inbound**, not A and not provision.

- Hosted: platform POSTs to Caspian; `cx.run(app)` receives Events.
- Process: platform POSTs to `cx.webhooks.telegram` (Next.js); same `parse` + overlap + `step`.
- Memory: you pass a fixture; no HTTP.

Provision may *register* `setWebhook(url)` during `channels.add`. Handling the
POST is always the interpreter + adapter `parse`.

---

## 7. Provisioning vs webhooks (three flows, don’t conflate)

`channels.add` drives a state machine: `requested → provisioning → active`.
Default `via` is `hosted`.

| `via` | Path to `active` |
|---|---|
| `hosted` (default) | allocate Caspian identity → maybe `authorizeUrl` (Slack/Discord) → platform **redirects to gateway callback** → `setWebhook` at Caspian → active |
| `self-host` | validate *your* secret (`getMe`) → store → `setWebhook` at **your** `webhookUrl` (or `inbound: false` for send-only) → active |

```text
1. PROVISIONING CALLBACK  platform → gateway   OAuth redirect; mutates Connection; you never handle it
2. NORMAL WEBHOOK         platform → gateway   Telegram Update → verify, ACK, overlap → Event → App
3. LIFECYCLE WEBHOOK      gateway → your app   connection.active / message.received push (or SSE)
```

#1 belongs to provision. #2 belongs to the runtime (adapter `parse`). #3 is
optional push so you don’t poll; in B it’s `cx.channels.on("active", …)`.
Process mode: #2 hits your own route (`cx.webhooks.telegram`); #3 disappears.

## 8. Example: AI tutor (Discord + Telegram + Meet)

```ts
const cx = new Caspian({
  adapters: { telegram: telegram(), discord: discord(), meet: meet() },
  hosted: { apiKey: process.env.CASPIAN_API_KEY },
});

// provisioning (idempotent)
const dc = await cx.channels.add("discord", { displayName: "Maya" }); // hosted
// dc.authorizeUrl → teacher clicks → provisioning callback → active
await cx.channels.add("telegram");                            // hosted bot / start link
const room = await cx.channels.add("meet");                   // room.joinUrl
cx.channels.on("active", (c) => log(`live on ${c.channel} as ${c.address}`));

// behavior (desugars to A Rules)
cx.onMessage({ channel: ["discord", "telegram"], overlap: "queue" },
  async (thread, msg, { skipped }) => {
    await thread.subscribe();
    await thread.typing();
    const a = await tutor.run({ history: await thread.recent(20),
                                messages: [...skipped, msg],
                                tools: cx.tools(thread) });
    await thread.post(a.text, { actions: [button("Got it", "ack")] });
  });

cx.onMessage({ channel: "meet", overlap: "drop" },   // voice: barge-in, never queue
  async (thread, utterance) => {
    await thread.post((await tutor.run({ messages: [utterance] })).text); // TTS via adapter
  });

cx.onAction({ overlap: "drop" }, async (thread, act) => {
  // telegram adapter already answered the callback query
  if (act.data === "ack") await thread.react("✅");
});

await cx.run();   // hosted: gateway owns normal webhooks, pushes Events
// tests: MemoryInterpreter().run(cx.app, fixtures.telegramDm("hi"))
```

Flow per message (hosted): Telegram POST → verify+ACK → overlap `queue(chat_id)`
→ Event → `step(cx.app)` → `[Subscribe, Typing, Host(tutor)]` → `Post` → outbox
→ adapter `sendMessage`. Meet: `parse` = STT, `execute(Post)` = TTS, `drop`.

## 9. Vercel AI SDK integration (`@caspian/ai`)

No `App` required. Two pieces over the same runtime: `cx.tools(thread)` (AI SDK
`tool()` set derived from A Commands) and inbound wrapped for Route Handlers.

| Mode | Inbound | Secrets | Code |
|---|---|---|---|
| **Hosted** | gateway receives platform webhooks, pushes Events to your URL | gateway | `verifyEvent(req)` → `generateText` → `thread.post` |
| **Non-hosted** | platform POSTs to your route | your env + Redis | `cx.handleWebhook(req, cb)` — same pipeline as `cx.run()` |
| **Outbound-only** | none | either | `cx.tools({ preset: "outbound" })` in a server action |

Mixing is per-connection (`add("discord")` hosted + `add("telegram", { via:
"self-host", ... })` is fine). Hosted inbound lands on the gateway; self-host
inbound lands on you. Never both for one connection.

## 10. Webhook registration, by use case

Two hops that look the same and are not:

```text
PLATFORM WEBHOOK     Telegram/Discord/Slack  ──POST──▶  whoever owns inbound
EVENT DELIVERY       Caspian gateway         ──POST──▶  your app  (hosted only)
OAUTH CALLBACK       Discord/Slack           ──GET───▶  gateway   (provisioning)
```

Only **one** owner of the platform webhook per connection. Registration
happens inside `channels.add` (or adapter `initialize` when non-hosted).

### Shared URL map (hosted Caspian)

```text
https://api.trycaspianai.com/webhooks/telegram/{bot_id}     ← Telegram setWebhook
https://api.trycaspianai.com/webhooks/discord               ← Discord interactions (shared app)
https://api.trycaspianai.com/webhooks/slack/{team_id}       ← Slack events
https://api.trycaspianai.com/oauth/discord/callback         ← provisioning, not messages
https://api.trycaspianai.com/oauth/slack/callback
```

Your app, if hosted, only ever exposes **one** URL Caspian can reach:

```text
https://maya.example.com/api/caspian                       ← event delivery
```

### Tutor — fully hosted

Maya’s code runs on a worker. Telegram never talks to that worker.

```ts
const cx = new Caspian({
  adapters: { telegram: telegram(), discord: discord(), meet: meet() },
  hosted: { apiKey: KEY, eventsUrl: "https://maya.example.com/api/caspian" },
});

await cx.channels.add("telegram");   // hosted — gateway setWebhook → Caspian URL
await cx.channels.add("discord", { displayName: "Maya" });
await cx.channels.add("meet");

await cx.run();
```

Runtime path:

```text
Student ──Telegram──▶  api.trycaspianai.com/webhooks/telegram/712345
                         verify secret, ACK 200, persist, overlap
                         POST https://maya.example.com/api/caspian
                           { type: "message", thread, message } + HMAC
Your worker ──verifyEvent──▶  tutor ──thread.post──▶  gateway outbox
                         ──sendMessage──▶ Telegram
```

You never call `setWebhook`. You never see a Telegram signature. Meet has no
registerable HTTP webhook; the meet adapter is subscribed to the room on the
gateway.

### Tutor — Telegram non-hosted, Discord hosted (mixed)

```ts
const cx = new Caspian({
  adapters: {
    telegram: telegram({ token: TG }),          // BYO, inbound = you
    discord: discord(),                         // hosted
  },
  hosted: { apiKey: KEY },                      // still used for Discord + outbox
  state: redisState(REDIS),                     // overlap for the Telegram path
});

await cx.channels.add("telegram", {
  via: "self-host",
  botToken: TG,
  webhookUrl: "https://maya.example.com/api/webhooks/telegram",
});
// adapter/gateway calls Telegram setWebhook with *your* URL, not Caspian's.

await cx.channels.add("discord", { displayName: "Maya" }); // hosted
```

```ts
// app/api/webhooks/telegram/route.ts  — Telegram talks to YOU
export async function POST(req: Request) {
  return cx.webhooks.telegram(req);   // parse, overlap on Redis, run App rules
}

// Discord messages still: Discord → Caspian → eventsUrl (if set) or cx.run() poll
```

Two inbound owners, two URLs, one `App`.

### Vercel AI SDK — hosted

Same registration as the hosted tutor. The Route Handler is the *event
delivery* URL, not a Telegram webhook.

```ts
// provisioning, once (script or first deploy)
const cx = new Caspian({ hosted: { apiKey: KEY } });
await cx.channels.add("telegram"); // hosted
await cx.channels.setEventsUrl("https://myapp.vercel.app/api/caspian");
```

```ts
// app/api/caspian/route.ts
export async function POST(req: Request) {
  const { thread, messages } = await verifyEvent(req, { apiKey: KEY });
  const result = await generateText({
    model: "anthropic/claude-sonnet-4",
    messages,
    tools: cx.tools(thread),
  });
  if (result.text) await thread.post(result.text);
  return new Response("ok");
}
```

```text
Telegram ──▶ api.trycaspianai.com/webhooks/telegram/{id}   (platform webhook)
               ──▶ myapp.vercel.app/api/caspian            (event delivery)
```

Vercel cold start is fine: Telegram already got a 200 from Caspian. If your
function is slow, overlap still held on the gateway.

### Vercel AI SDK — non-hosted

You register with Telegram yourself (or let the adapter do it on boot).
Caspian cloud optional / unused.

```ts
const cx = new Caspian({
  adapters: { telegram: telegram({ token: TG, secret: SECRET }) },
  state: redisState(REDIS),
});

await cx.channels.add("telegram", {
  via: "self-host",
  botToken: TG,
  webhookUrl: "https://myapp.vercel.app/api/webhooks/telegram",
});
```

```ts
// app/api/webhooks/telegram/route.ts
export async function POST(req: Request) {
  return cx.handleWebhook(req, async ({ thread, messages }) => {
    const result = await generateText({ model, messages, tools: cx.tools(thread) });
    if (result.text) await thread.post(result.text);
  });
}
```

```text
Telegram ──▶ myapp.vercel.app/api/webhooks/telegram     (only hop)
               verify X-Telegram-Bot-Api-Secret-Token
               ACK, overlap on Redis, generateText, sendMessage
```

No `/api/caspian`. No `CASPIAN_API_KEY`. You must 200 Telegram quickly
(`waitUntil` / `after()` for the model call) — Caspian is not there to ACK
for you.

### Coding agent — outbound only

```ts
await cx.channels.add("telegram", {
  via: "self-host",
  botToken: TG,
  inbound: false,                 // send-only: do not setWebhook
});
```

No platform webhook. No event URL. Tools call `thread.post` → outbox / adapter
send only.

### What gets registered, cheat sheet

| Use case | Who calls `setWebhook` / Slack request URL | URL registered | Your public route |
|---|---|---|---|
| Tutor hosted | gateway, during `channels.add` | `api.trycaspianai.com/webhooks/telegram/{id}` | `/api/caspian` (Caspian → you) |
| Tutor mixed TG | adapter, `via: "self-host"` | `maya.example.com/api/webhooks/telegram` | that route + maybe `/api/caspian` for Discord |
| AI SDK hosted | gateway, default `add("telegram")` | Caspian URL | `/api/caspian` |
| AI SDK non-hosted | `via: "self-host"` | your Vercel URL | `/api/webhooks/telegram` |
| Outbound only | nobody (`via: "self-host"`, `inbound: false`) | none | none |
| Discord hosted (all) | already set on the shared Discord app | Caspian `/webhooks/discord` | none extra |
| OAuth click | n/a | `api.trycaspianai.com/oauth/{channel}/callback` | none — browser redirect |

## 11. Dependency rule

```text
A core          → nothing Caspian, no HTTP
Adapters        → A Event/Command types + platform HTTP
B facade        → A + adapters
Provision       → HTTP control plane only
Hosted executor → provision Connection + adapter.execute via outbox
```

Forbidden: A importing provision or `@caspian/telegram`.
Forbidden: B APIs that cannot desugar to A.
