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

```bash
bun install
bun run ci
```
