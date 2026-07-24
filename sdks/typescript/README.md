# caspian-sdk

**Give your AI agent one identity that reaches any human, on whatever app they already use** — email, Slack, Discord, WhatsApp, SMS, X, Telegram, iMessage — all behind a single `onMessage` handler.

You write the handler once. Caspian handles the provider quirks, threading, delivery, and dedup for every channel.

```bash
npm install caspian-sdk
```

Zero runtime dependencies. TypeScript types included. Node 18+ (uses native `fetch`).

## Quickstart

```ts
import { CommClient } from "caspian-sdk";

const client = new CommClient({ apiKey: "YOUR_KEY" });

// Connect any channel — email needs nothing; others take a token or one-click OAuth.
const inbox = await client.connectEmail();
console.log("Agent address:", inbox.address);

client.onMessage(async (message) => {
  // The same handler answers every channel you connect.
  await message.reply(`Thanks! You said: ${message.text}`);
});

await client.listen(); // one loop, every channel
```

`apiKey` and `baseUrl` fall back to `CASPIAN_API_KEY` / `CASPIAN_BASE_URL` from the environment or a local `.env`, so `new CommClient()` with no arguments works too.

## Channels

| | Connect |
|---|---|
| **Email** | `connectEmail()` — default domain or your own |
| **Slack** | `installSlack()` (one-click) or `connectSlack({...})` (your own app) |
| **Discord** | `installDiscord()` (one-click) or `connectDiscord({...})` |
| **X / Twitter** | `installX()` (one-click) or `connectX({...})` |
| **WhatsApp** | `connectWhatsapp({...})` (Caspian hosted) |
| **SMS / phone** | `connectPhone({...})` — own GSM modem, or Caspian hosted |
| **Telegram** | `connectTelegram({ botToken })` |
| **iMessage** | `connectImessage()` |

OAuth channels (Slack/Discord/X/Instagram/Facebook) return a connection with an `authorize_url` — hand it to the user; the connection flips to `active` once they approve.

## Make your agent platform-aware

Each channel behaves differently (Slack threads, WhatsApp's 24-hour window, SMS length, iMessage has no markdown). Pull per-channel etiquette for the channels you connected and drop it into your agent's system prompt:

```ts
const guide = await client.behaviorPrompt();
systemPrompt += "\n\n" + guide;
// or one channel: await client.channelGuide("slack")
```

Use it, tweak it, or ignore it and write your own.

## Rich messages

Send one provider-neutral `blocks` payload and each channel gets its best
rendering — Slack, Discord and Telegram render natively, email gets rich HTML,
and text-only channels degrade to clean text automatically.

```ts
import type { Block } from "caspian-sdk";

const blocks: Block[] = [
  {
    type: "card",
    title: "Order #1024 shipped",
    subtitle: "Arriving Thursday",
    buttons: [
      { label: "Track", url: "https://example.com/track/1024" },
      { label: "Get help", value: "help:1024" }, // callback
    ],
  },
];

await message.reply(undefined, undefined, blocks);
// or proactively: await client.sendMessage(conversationId, null, null, blocks);
```

Block types: `heading`, `text`, `divider`, `image`, `fields`, `list`, `buttons`,
`card`. A button with a `url` is a link; a button with a `value` is a callback.

## Streaming replies

Caspian supports streaming responses token by token. If a channel supports post+edit (like Telegram or Discord), it posts a placeholder immediately and updates it in place. On other channels (like Email or SMS), it buffers the stream and sends a single final reply on close().

Always call `.close()` in a `finally` block to ensure the stream is flushed.

```ts
client.onMessage(async (message) => {
  const stream = message.stream();
  try {
    for await (const chunk of llmStream(message.text)) {
      await stream.append(chunk);
    }
  } finally {
    await stream.close();
  }
});
```

## How it works

- **One handler, every channel.** Adding a channel is another `connect*()` call — never new handler code.
- **`message.reply()`** answers in the right thread on the right channel automatically.
- **`message.typing()`** shows a "typing…" indicator while your agent thinks (where the platform supports it).
- **`client.listen()`** is resilient — a handler error or a dropped poll won't stop the loop. Pass an `AbortSignal` to stop it:

```ts
const ac = new AbortController();
client.listen({ signal: ac.signal });
// later: ac.abort();
```

## Errors

Non-2xx responses throw a `CommError` with `statusCode` and `detail`:

```ts
import { CommError } from "caspian-sdk";

try {
  await client.connectX({ accessToken, userId });
} catch (err) {
  if (err instanceof CommError && err.statusCode === 402) {
    // Paid channel — sign in first. err.detail explains how.
  }
}
```

## Docs

Point your coding agent at the setup guide and it does the whole integration for you. Full docs and your API key: **[trycaspianai.com](https://trycaspianai.com)**.

## License

MIT
