# Phase 4 — B facade design

Date: 2026-08-16
Status: write-up (not implemented)
Branch: `feat/rewrite-ts`
Depends on: Phases 0–3 (A-core Schema, `step()`, Memory interpreter)

This is the design for the first public API. It does not add kernel
constructors. It does not talk to Telegram.

Companion plan: `docs/superpowers/plans/2026-08-16-phase-4-b-facade.md`

---

## 1. Goal

A bot author can write Chat SDK-shaped TypeScript that **desugars** into the
A program already implemented:

```ts
const cx = new Caspian()

cx.onMessage(
  { channel: "telegram", kind: "dm", overlap: "queue" },
  async (thread, msg, { skipped }) => {
    await thread.typing()
    await thread.post(`echo:${msg.text}`)
  },
)

// Memory driver — no network
const mem = await cx.interpret({ channel: "telegram" })
await mem.run(dmEvent)
// recorded commands include Post { text: "echo:hello" }
```

After `onMessage` returns, an `App` value exists. An Event does not.

Success is not “handlers run.” Success is: **every public verb produces an A
constructor, proven by a golden vector.**

---

## 2. Denotation (meaning before syntax)

B is a derived language over A. Same move as Languages-as-Libraries: expand
sugar to a tiny core IR, then only analyze the IR.

| Surface (syntax) | Meaning (A) |
|---|---|
| `onMessage(opts, fn)` | one `Rule` (`kind=message` ∧ filters) + `Host(handler_id)` |
| `onAction(opts, fn)` | one `Rule` (`kind=action` ∧ filters) + `Host(handler_id)` |
| `cx.use({ predicate, overlap }, fn)` | one `Rule` as written (escape hatch) |
| `thread.post(text)` | enqueue `{ tag: "Post", thread_id, text }` |
| `thread.typing()` | enqueue `{ tag: "Typing", thread_id }` |
| `thread.edit(id, text)` | enqueue `{ tag: "Edit", ... }` |
| `thread.react(id, emoji)` | enqueue `{ tag: "React", ... }` |
| `cx.program` | the `App` `{ rules }` |
| `fn` | HostPort implementation for that `handler_id` — **not** the program |

Laws:

1. **Desugar law.** A public registration that does not append a `Rule` is a
   bug. A `Map<string, fn>` with no `Rule` is how the kernel dies.
2. **Command law.** `thread.*` never calls a platform. It appends a `Command`
   to the current turn buffer. HostPort returns that buffer as data.
3. **Channel law.** Facade code never branches on platform names. `channel`
   is a string (or list of strings) copied into `MatchChannel`.
4. **Parse law.** Options objects are Schema-decoded with
   `onExcessProperty: "error"`. Extra keys fail. Types are not enough.
5. **Default law.** Omitting `overlap` does not mean “no policy.” Messages
   default to `queue` / bound `16`. Actions default to `drop` / bound `16`.

Phase 4 adds **zero** Event, Command, or Predicate constructors.

---

## 3. The `kind` trap (read twice)

On `onMessage` / `onAction`, `kind` means **chat kind**, not event kind.

```ts
onMessage({ kind: "dm" }, fn)
// event kind  = "message"     ← implied by the method name
// chat_kind   = "dm"          ← the options field
```

`kind: "text"` is a decode error. Event kinds are chosen by which method you
called (`onMessage` → `message`, `onAction` → `action`).

This matches `docs/caspian-a-plus-b.md` and the PRD. It is the option people
will misread first.

---

## 4. Approaches considered

**A — Handler map only, Memory.register later.**  
`onMessage` stores `fn`. App is empty until someone remembers to build rules.
Rejected: Memory would “work”; Process/Hosted would lie. Violates the one rule.

**B — Desugar to `App` + a B `HostPort` Layer. Memory stays as-is.**  
`onMessage` appends a `Rule` and records `fn` under `handler_id`.
`interpret()` builds the Phase 3 Memory interpreter with
`host: bHostLayer(handlers)`. Phase 3 tests keep their sync `HostFn`.
**Chosen.**

**C — Change Memory `HostFn` to async and have Caspian call `register()`.**  
Works, but churns Phase 3 and couples the facade to Memory’s register API.
The Host port already exists so B can supply its own Layer.

Thread approaches:

- Return `Command[]` from the user handler — not Chat SDK shaped.
- **Turn buffer:** `thread.post` appends; HostPort returns the buffer. Chosen.
- User writes Effect and yields commands — too Effect-flavored for the README.

---

## 5. Public API

```ts
type ChatKind = "dm" | "group" | "channel"
type OverlapPolicy = "queue" | "debounce" | "drop" | "parallel"

type OnMessageOptions = {
  readonly channel?: string | ReadonlyArray<string>
  readonly kind?: ChatKind          // chat_kind, not event kind
  readonly overlap?: OverlapPolicy  // default "queue"
  readonly bound?: number           // default 16
}

type OnActionOptions = {
  readonly channel?: string | ReadonlyArray<string>
  readonly overlap?: OverlapPolicy  // default "drop"
  readonly bound?: number           // default 16
}

type HostContext = { readonly skipped: ReadonlyArray<Event> }

type MessageHandler = (
  thread: Thread,
  message: Message,
  ctx: HostContext,
) => void | Promise<void>

type ActionHandler = (
  thread: Thread,
  action: Action,
  ctx: HostContext,
) => void | Promise<void>

type Thread = {
  readonly id: ThreadId
  post(text: string, options?: { actions?: ReadonlyArray<PostAction> }): Promise<void>
  typing(): Promise<void>
  edit(messageId: string, text: string): Promise<void>
  react(messageId: string, emoji: string): Promise<void>
}

class Caspian {
  onMessage(handler: MessageHandler): this
  onMessage(options: OnMessageOptions, handler: MessageHandler): this

  onAction(handler: ActionHandler): this
  onAction(options: OnActionOptions, handler: ActionHandler): this

  /** A escape hatch. Not in the README. */
  use(rule: { predicate: Predicate; overlap?: Overlap }, handler: MessageHandler | ActionHandler): this

  readonly program: App

  interpret(options?: { channel?: string }): Promise<MemoryInterpreter>
}
```

`interpret` is the Phase 4 driver (Memory). Process/Hosted replace it later
without changing `onMessage` or `thread.post`.

Overloads: `onMessage(fn)` equals `onMessage({}, fn)`.

`handler_id` is deterministic: `onMessage:0`, `onMessage:1`, `onAction:0`,
`use:0` — per-method counters, zero-based, registration order.

---

## 6. Desugar

Left-associated `and`, fixed order so vectors are stable:

1. `{ op: "kind", kind: "message" | "action" }`  (always)
2. `{ op: "channel", channels }`                 (if `channel` present)
3. `{ op: "chat_kind", chat_kind }`              (if `kind` present on onMessage)

```text
onMessage({ channel: ["discord", "telegram"], kind: "dm", overlap: "queue" }, fn)

→ Rule {
    predicate: and(
      and({ op: "kind", kind: "message" },
          { op: "channel", channels: ["discord", "telegram"] }),
      { op: "chat_kind", chat_kind: "dm" }
    ),
    overlap: { policy: "queue", bound: 16 },
    handler_id: "onMessage:0"
  }
```

`channel: "telegram"` becomes `channels: ["telegram"]`.

`onAction({ overlap: "drop" }, fn)` with no channel:

```text
Rule {
  predicate: { op: "kind", kind: "action" },
  overlap: { policy: "drop", bound: 16 },
  handler_id: "onAction:0"
}
```

`cx.use` does not invent filters. It copies the predicate. Missing overlap
defaults to `{ policy: "queue", bound: 16 }`.

Options decode through Effect Schema + `decodeStrict`. Illegal `kind`,
`overlap`, `bound < 1`, or extra keys → `DecodeError`. `onMessage` /
`onAction` surface that as a thrown `DecodeError` at registration time
(author mistake, not a runtime Event). The kernel still never throws.

---

## 7. Thread and HostPort

```text
step()                         HostPort (B layer)
  Typing + Host(id)              make Thread(event.thread_id, sink)
                                 await userFn(thread, event, ctx)
                                 return sink.commands
```

`step` still does not call `fn`. It still emits `{ tag: "Host", handler_id }`.
The B Layer looks up `fn`, builds a Thread whose methods only `sink(command)`,
awaits the handler, returns the collected `Command[]`.

If the handler throws, fold into `HostError` (same as the chaos layer).
If `onMessage` is invoked with a non-`message` Event (predicate bug),
`HostError` — do not run `fn`.

`thread.typing()` is a user-enqueued `Typing`. The kernel already emits one
`Typing` before `Host`. Two Typings is correct (automatic + explicit).

Out of Thread for this phase: `recent()`, `state`, `subscribe()`, `native`,
`setState`. Those need relationship memory or `Call` / adapter methods.

---

## 8. File map

All new code under `packages/typescript/`. Core is not modified except
re-exports if the public barrel needs types.

| File | Responsibility |
|---|---|
| `src/facade/options.ts` | Schema for `OnMessageOptions` / `OnActionOptions` |
| `src/facade/desugar.ts` | options + handler_id → `Rule` (pure) |
| `src/facade/thread.ts` | `makeThread(id, sink)` — Command collector |
| `src/facade/host.ts` | `bHostLayer(handlers)` — HostPort for B handlers |
| `src/facade/caspian.ts` | `Caspian` class |
| `src/facade/index.ts` | facade barrel |
| `src/index.ts` | public export = facade (+ needed types). Not `src/core`. |
| `test/desugar.test.ts` | golden `vectors/desugar_vectors.json` replay |
| `test/thread.test.ts` | post/typing/edit/react enqueue, never HTTP |
| `test/facade.test.ts` | overloads, interpret + Memory e2e, HostError, channel filter |
| `vectors/desugar_vectors.json` | options in → `App` JSON out (shared with Python later) |
| `README.md` | `onMessage` first. No combinators. No `cx.use`. |

`dependency-cruiser` already forbids `core → facade`. No rule change required
unless we add `facade ⊬ adapters` (adapters do not exist yet).

---

## 9. Out of scope (Phase 5+)

- Telegram / any adapter (`parse` / `execute` / HTTP)
- `channels.add` and `provision/`
- Process and Hosted interpreters
- `onReaction`, `onTyping` as public verbs
- `thread.recent` / relationship memory
- `@caspian/ai` tools
- CLI
- Teaching `on(message & ~dm())` in the README

If a Phase 4 PR adds any of these, it is out of scope, not “ahead.”

---

## 10. Acceptance

Must pass `cd packages/typescript && bun run ci` plus:

1. Every `desugar_vectors.json` case: options → `decodeApp` equals expected.
2. `onMessage({ kind: "dm" })` program matches a message+chat_kind rule, not
   `{ op: "kind", kind: "dm" }`.
3. Extra option key → `DecodeError`.
4. `thread.post("hi")` recorded command is `Post`; no `fetch` / `node:http`.
5. Fixture DM through `interpret` + `run` records that `Post`.
6. `onMessage({ channel: "discord" })` + `interpret({ channel: "telegram" })`
   → `unmatched`, no Host.
7. Handler throw → `HostError` on the interpreter, no throw out of `step`.
8. `src/index.ts` exports `Caspian`. App code is not told to import `src/core`.
9. README example is `onMessage` / `thread.post`, not predicate combinators.

---

## 11. Review gates (from caspian-sdk-surface)

- [ ] Each new public method maps to an A constructor + a vector
- [ ] Surface is option-shaped, not combinator-shaped
- [ ] `thread.*` enqueues Commands
- [ ] No platform names in facade control flow
- [ ] `cx.use` exists and is absent from the README
- [ ] Adding WhatsApp later requires zero facade API changes
