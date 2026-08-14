---
name: caspian-sdk-surface
description: >-
  Review and enforce the Caspian B surface: a Chat SDK-shaped public API that
  desugars into the A kernel, adapters as the only channel-aware code, hosted-
  default provisioning, and tools as a view. Use when adding a public method,
  writing an adapter, changing channels.add, exposing agent tools, or checking
  that the SDK still follows the B plan.
---

# Caspian SDK surface (plan B)

Applies to everything a user imports: the `Caspian` facade, adapters,
provisioning, tools, CLI. The kernel rules live in `caspian-core-discipline`.

## The one rule

> Every public API must desugar into an A constructor.

If `onTyping(fn)` only stores a closure and has no `Rule` behind it, the kernel
is no longer the program and the whole A investment is dead. No exceptions,
including "just this once for a demo".

Gate for every PR that adds public surface:

1. Which A constructor does it produce?
2. Where is the golden vector showing the desugared `App`?
3. Can the same behavior already be expressed? (Prefer options over new verbs.)

## Public API shape (steal Chat SDK)

Familiar first. Users should not learn an algebra to answer a DM.

```ts
cx.onMessage(
  { channel: ["discord", "telegram"], kind: "dm", overlap: "queue" },
  async (thread, msg, { skipped }) => {
    await thread.typing();
    await thread.post(await agent.run(msg, skipped));
  },
);

cx.onAction({ overlap: "drop" }, handler);
```

Rules:

- **Options objects, not predicate combinators**, on the public surface.
  `{ channel, kind, overlap }` — not `message & channel("telegram") & ~dm()`.
- Handlers receive `(thread, event, ctx)`. `thread` methods enqueue Commands;
  they are not raw HTTP calls.
- The escape hatch to raw A (`cx.use(on(...))`) exists but is **not** in the
  first README, the quickstart, or any error message.
- Naming follows the ecosystem people came from. Do not invent a synonym for a
  concept Chat SDK already named.

## Adapters

The only code that knows a platform exists.

### Required vs optional

Required: `parse`, `execute` (post/edit/react/typing), `encodeThreadId` /
`decodeThreadId`, `overlapKey`, `capabilities`, `format`.

Optional: photos, modals, ephemeral, streaming, native extras. A missing
optional is **typed absent** (`openModal?: never` / not implemented), never a
stub that silently no-ops or throws at runtime. Degradation belongs in the
Thread layer (buttons → numbered list on SMS), not in the kernel.

### Adapter laws

- **Ack law.** If the platform requires an acknowledgement for an interaction
  (Telegram `answerCallbackQuery`), the *adapter* sends it while executing that
  turn. A user forgetting it must be impossible.
- **Key law.** `overlapKey` reflects the platform's real conversation unit
  (Telegram: chat; Slack: channel+thread). Wrong key = merged or split
  conversations.
- **Parse law.** Unknown update types return `[]`, never throw, never a
  half-built Event. Unmapped payload stays on `raw`.
- **Format law.** Rendering (MarkdownV2, Block Kit, plain) happens in the
  adapter's format converter, not in handler code and not in core.
- **No decisions.** An adapter never chooses whether to reply, queue, or
  subscribe. It translates.

### Coverage without leaking

- Shared concept on ≥2 channels → promote to abstract Event/Command.
- Single-platform concept → typed method on the adapter (`telegram.sendPhoto`),
  reachable from host code or `Call`, absent from the kernel.
- Adding a channel = new adapter package + CLI namespace. If it also needs a
  new public handler type, the abstraction is wrong.

## Provisioning surface

- **One verb:** `cx.channels.add(channel, options?)`.
- **Hosted is the default.** Omitting `via` means Caspian owns the identity and
  inbound. It never means "I forgot a token".
- **`via: "self-host"`** is the only opt-in; it requires the channel's secret
  (and usually `webhookUrl`). Missing secret is an error, not a fallback.
- No `via: "credentials"` / `via: "oauth"`. OAuth is how hosted channels finish
  going live, not a caller-chosen recipe.
- Exactly **one inbound owner per connection**, recorded at add time. Consuming
  inbound for a connection you do not own must fail loudly.
- Provisioning code may not import core, and core may not import provisioning.

## Tools (agent view)

- Derived from Command types — not a hand-maintained parallel schema file.
- Small default set (`post_message`, `edit_message`, `add_reaction`,
  `start_typing`, `send_dm`). Presets (`messenger`, `outbound`) filter it.
- Models address a **thread id**, never a platform chat id.
- Native platform methods only via explicit opt-in packs.
- Tools are an interpreter of the same Commands; a tool that bypasses the
  Command path and calls the platform directly is a bug.

## Layering rule

```text
core        → nothing Caspian, no HTTP
adapters    → core types + platform HTTP
facade (B)  → core + adapters
provision   → control plane HTTP only
tools/CLI   → facade + command schemas
```

Forbidden: core → adapters, core → provision, facade logic that never reaches
core, two implementations of the inbound pipeline (webhook helper and `run`
must call the same one).

## Review checklist

- [ ] Does each new public method map to an A constructor + a vector?
- [ ] Is the surface option-shaped (familiar) rather than combinator-shaped?
- [ ] Do `thread.*` methods enqueue Commands instead of calling the platform?
- [ ] Does the adapter satisfy the ack / key / parse / format laws?
- [ ] Is an unsupported capability typed as absent, not stubbed?
- [ ] Is `channels.add` hosted by default, with `via: "self-host"` the only opt-in?
- [ ] Is there exactly one inbound owner per connection, enforced?
- [ ] Are tools derived from Commands, with thread ids (not chat ids)?
- [ ] Does the inbound pipeline have exactly one implementation?
- [ ] Would adding another channel require zero changes to the public API?

## Red flags

- A public API added "temporarily" without a kernel constructor.
- Platform names in facade code (`if (channel === "telegram")`).
- A second webhook pipeline for a framework integration.
- `botToken` inferred, defaulted, or read from env inside `channels.add`.
- Tool definitions written by hand in a separate file from Commands.
- An adapter that queues, retries, or decides whether to reply.
- Docs that teach `on(message & ~dm())` before `onMessage({ kind })`.
