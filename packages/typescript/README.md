# caspian (TypeScript)

Rewrite of the Caspian TypeScript SDK. You write a Chat SDK-shaped API. It
desugars into a small Effect kernel. This package is not the legacy
`CommClient`.

```ts
import { Caspian } from "caspian"

const cx = new Caspian()

cx.onMessage(
  { channel: "telegram", kind: "dm", overlap: "queue" },
  async (thread, msg) => {
    await thread.typing()
    await thread.post(`echo:${msg.text}`)
  },
)
```

`kind` here is the chat kind (`dm` / `group` / `channel`). The method name
already means “message events.”

`thread.post` enqueues a `Post` command. It does not call Telegram.

Telegram lives in `caspian/telegram`: `parseTelegramUpdate` turns an Update
into Events; `planTurn` turns Commands into Bot API method bodies.

The other channels are the same shape — import the pack, not the facade:

```ts
import { discord, discordHttpLayer } from "caspian/discord"
import { slack, slackHttpLayer } from "caspian/slack"
```

Also: `caspian/voice`, `caspian/email`, `caspian/sms`, `caspian/whatsapp`,
`caspian/messenger`, `caspian/imessage`, `caspian/x`, `caspian/linear`.
Each pack parses platform bytes into kernel Events, plans Commands into
platform calls, and owns overlap keys / thread ids. Unsupported kernel
commands fail as `AdapterError`. WhatsApp delivery receipts are not kernel
Events (no `Receipt` constructor) — parse returns `[]`.

Self-host inbound: `await cx.listen({ adapter: telegramHttpLayer(), ... })`
then `POST` → `cx.webhooks.telegram(req)`. The platform is ACKed 200 before
the model; a wrong secret is 401. Tests inject a recording Layer — no live
network in CI.

Hosted inbound: `await cx.run({ adapter: hostedHttpLayer(), webhookSecret, ... })`
then `POST` → `cx.webhooks.caspian(req)`. The body is a kernel Event envelope,
signed with HMAC-SHA256 (`X-Caspian-Signature`). Execute goes to the Caspian
outbox, not `api.telegram.org`.

`via` is required on `channels.add`. There is no omit-means-hosted:

```ts
await cx.channels.add("telegram", { via: "hosted" })
await cx.channels.add("telegram", {
  via: "self-host",
  botToken: token,
  webhookUrl: "https://myapp.example.com/api/webhooks/telegram",
})
```

`"hosted" | "self-host"` only. Not `oauth`, not `credentials`. Self-host inbound
without `webhookUrl` is an error; `inbound: false` is send-only. This mints a
`Connection`. It does not call a live gateway yet.

`cx.tools(thread)` / `cx.tools({ preset: "outbound" })` is the same Command
surface as `thread.post` for models. Parameters come from Command schemas.
Thread ids only — never a platform chat id. `Host` and `Call` are not tools.

`thread.recent(n)` and `thread.state` are runner memory. Handlers ask; Memory
(and Process/Hosted on top of it) stores inbound Events and `SetState`. A
thread with no store returns `[]` / `undefined`.

```bash
bun install
bun run ci
```
