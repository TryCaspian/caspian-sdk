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

`thread.post` enqueues a `Post` command. It does not call Telegram. Channel
HTTP lands in adapters (later).

```bash
bun install
bun run ci
```
