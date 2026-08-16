# Phase 4 B Facade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Chat SDK-shaped `Caspian` facade whose every public method desugars into an existing A `Rule` / `Command`, proven by golden vectors and a Memory e2e with no network.

**Architecture:** B is syntax. `desugarOnMessage` / `desugarOnAction` are pure functions from options + `handler_id` to a `Rule`. `thread.*` appends `Command`s to a turn sink. `bHostLayer` is the HostPort that runs the author’s async handler and returns that sink. `Caspian.interpret` boots the Phase 3 Memory interpreter with that Layer. Core is not extended.

**Tech Stack:** bun, Effect 3.22, Effect Schema, existing `packages/typescript` workspace, shared `vectors/*.json`.

## Global Constraints

- Every public API must desugar into an A constructor (no closure-only methods).
- `kind` on `onMessage` options is chat kind (`dm` | `group` | `channel`), never event kind.
- Options decode with `decodeStrict` / `onExcessProperty: "error"`.
- `thread.*` enqueues Commands; it never calls HTTP or names a platform.
- Facade must not `if (channel === "telegram")`.
- Default overlap: messages `queue` / 16, actions `drop` / 16.
- `handler_id` format: `onMessage:N`, `onAction:N`, `use:N` (zero-based, per method).
- Predicate fold is left-associated `and` in order: event kind, channel, chat_kind.
- Do not add Event/Command/Predicate constructors. Do not add adapters, provision, tools, `onReaction`, or `thread.recent`.
- `cx.use` is implemented and absent from `README.md`.
- bun, not npm. Tests: `cd packages/typescript && bun test`. Full gate: `bun run ci`.
- Wire JSON stays snake_case (`thread_id`, `handler_id`, `chat_kind`).
- Design spec: `docs/superpowers/specs/2026-08-16-phase-4-b-facade-design.md`.

---

## File map

| Path | Role |
|---|---|
| `packages/typescript/src/facade/options.ts` | Option Schemas |
| `packages/typescript/src/facade/desugar.ts` | options → `Rule` |
| `packages/typescript/src/facade/thread.ts` | Command-collecting `Thread` |
| `packages/typescript/src/facade/host.ts` | B `HostPort` Layer |
| `packages/typescript/src/facade/caspian.ts` | `Caspian` class |
| `packages/typescript/src/facade/index.ts` | barrel |
| `packages/typescript/src/index.ts` | public export |
| `packages/typescript/test/desugar.test.ts` | golden desugar replay |
| `packages/typescript/test/thread.test.ts` | Thread sink |
| `packages/typescript/test/facade.test.ts` | Caspian + Memory e2e |
| `vectors/desugar_vectors.json` | options → App |
| `packages/typescript/README.md` | `onMessage` first |

---

### Task 1: Options Schema and pure desugar

**Files:**
- Create: `vectors/desugar_vectors.json`
- Create: `packages/typescript/src/facade/options.ts`
- Create: `packages/typescript/src/facade/desugar.ts`
- Create: `packages/typescript/test/desugar.test.ts`

**Interfaces:**
- Consumes: `Overlap`, `OverlapPolicy`, `Bound`, `Rule`, `Predicate` from `src/core`; `decodeStrict` from `src/core/parse.ts`; `decodeApp` from `src/core/index.ts`
- Produces: `OnMessageOptions`, `OnActionOptions`, `decodeOnMessageOptions`, `decodeOnActionOptions`, `desugarOnMessage(options, handlerId) → Rule`, `desugarOnAction(options, handlerId) → Rule`

- [ ] **Step 1: Write the golden fixture**

Create `vectors/desugar_vectors.json`:

```json
[
  {
    "name": "onMessage with no options is message + queue/16",
    "method": "onMessage",
    "options": {},
    "handler_id": "onMessage:0",
    "expected_app": {
      "rules": [
        {
          "predicate": {"op": "kind", "kind": "message"},
          "overlap": {"policy": "queue", "bound": 16},
          "handler_id": "onMessage:0"
        }
      ]
    }
  },
  {
    "name": "onMessage kind is chat_kind, not event kind",
    "method": "onMessage",
    "options": {"channel": "telegram", "kind": "dm", "overlap": "queue"},
    "handler_id": "onMessage:0",
    "expected_app": {
      "rules": [
        {
          "predicate": {
            "op": "and",
            "left": {
              "op": "and",
              "left": {"op": "kind", "kind": "message"},
              "right": {"op": "channel", "channels": ["telegram"]}
            },
            "right": {"op": "chat_kind", "chat_kind": "dm"}
          },
          "overlap": {"policy": "queue", "bound": 16},
          "handler_id": "onMessage:0"
        }
      ]
    }
  },
  {
    "name": "onMessage channel list",
    "method": "onMessage",
    "options": {"channel": ["discord", "telegram"]},
    "handler_id": "onMessage:0",
    "expected_app": {
      "rules": [
        {
          "predicate": {
            "op": "and",
            "left": {"op": "kind", "kind": "message"},
            "right": {"op": "channel", "channels": ["discord", "telegram"]}
          },
          "overlap": {"policy": "queue", "bound": 16},
          "handler_id": "onMessage:0"
        }
      ]
    }
  },
  {
    "name": "onAction defaults to drop/16",
    "method": "onAction",
    "options": {},
    "handler_id": "onAction:0",
    "expected_app": {
      "rules": [
        {
          "predicate": {"op": "kind", "kind": "action"},
          "overlap": {"policy": "drop", "bound": 16},
          "handler_id": "onAction:0"
        }
      ]
    }
  },
  {
    "name": "onAction with overlap drop and channel",
    "method": "onAction",
    "options": {"channel": "telegram", "overlap": "drop"},
    "handler_id": "onAction:0",
    "expected_app": {
      "rules": [
        {
          "predicate": {
            "op": "and",
            "left": {"op": "kind", "kind": "action"},
            "right": {"op": "channel", "channels": ["telegram"]}
          },
          "overlap": {"policy": "drop", "bound": 16},
          "handler_id": "onAction:0"
        }
      ]
    }
  }
]
```

- [ ] **Step 2: Write the failing desugar tests**

Create `packages/typescript/test/desugar.test.ts`:

```ts
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { decodeApp } from "../src/core/index.ts"
import {
  decodeOnMessageOptions,
  desugarOnAction,
  desugarOnMessage,
} from "../src/facade/desugar.ts"

const vectorsUrl = new URL("../../../vectors/desugar_vectors.json", import.meta.url)

const runEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

test("golden desugar vectors replay options into App", async () => {
  const file = Bun.file(vectorsUrl)
  expect(await file.exists()).toBe(true)
  const vectors: unknown = await file.json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }
  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }
    const method = vector.method
    const handlerId = vector.handler_id
    expect(typeof handlerId).toBe("string")
    if (typeof handlerId !== "string") {
      continue
    }
    const options = vector.options
    const rule =
      method === "onAction"
        ? desugarOnAction(options, handlerId)
        : desugarOnMessage(options, handlerId)
    const expected = runEither(decodeApp(vector.expected_app))
    expect(Either.isRight(expected), String(vector.name)).toBe(true)
    if (Either.isLeft(expected)) {
      continue
    }
    expect({ rules: [rule] }, String(vector.name)).toEqual(expected.right)
  }
})

test("onMessage rejects extra option keys", () => {
  const result = runEither(decodeOnMessageOptions({ kind: "dm", evil: true }))
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("DecodeError")
})

test("onMessage rejects kind text (event kind is not an option)", () => {
  const result = runEither(decodeOnMessageOptions({ kind: "text" }))
  expect(Either.isLeft(result)).toBe(true)
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd packages/typescript && bun test test/desugar.test.ts`

Expected: FAIL — `src/facade/desugar.ts` cannot be resolved.

- [ ] **Step 4: Implement options + desugar**

Create `packages/typescript/src/facade/options.ts`:

```ts
import * as Schema from "effect/Schema"
import { Bound, OverlapPolicy } from "../core/app.ts"
import { ChatKind } from "../core/events.ts"

export const ChannelOption = Schema.Union(
  Schema.String,
  Schema.Array(Schema.String),
)

export const OnMessageOptions = Schema.Struct({
  channel: Schema.optional(ChannelOption),
  kind: Schema.optional(ChatKind),
  overlap: Schema.optional(OverlapPolicy),
  bound: Schema.optional(Bound),
})
export type OnMessageOptions = typeof OnMessageOptions.Type

export const OnActionOptions = Schema.Struct({
  channel: Schema.optional(ChannelOption),
  overlap: Schema.optional(OverlapPolicy),
  bound: Schema.optional(Bound),
})
export type OnActionOptions = typeof OnActionOptions.Type
```

Create `packages/typescript/src/facade/desugar.ts`:

```ts
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import type { Overlap, Rule } from "../core/app.ts"
import type { DecodeError } from "../core/errors.ts"
import type { Predicate } from "../core/predicates.ts"
import { decodeStrict } from "../core/parse.ts"
import {
  OnActionOptions,
  OnMessageOptions,
} from "./options.ts"

export const decodeOnMessageOptions = decodeStrict(OnMessageOptions)
export const decodeOnActionOptions = decodeStrict(OnActionOptions)

const unwrap = <A>(effect: Effect.Effect<A, DecodeError>): A => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isLeft(result)) {
    throw result.left
  }
  return result.right
}

const channelsOf = (
  channel: string | ReadonlyArray<string> | undefined,
): ReadonlyArray<string> | undefined => {
  if (channel === undefined) {
    return undefined
  }
  return typeof channel === "string" ? [channel] : channel
}

const andAll = (parts: ReadonlyArray<Predicate>): Predicate => {
  const first = parts[0]
  if (first === undefined) {
    return { op: "all" }
  }
  return parts.slice(1).reduce<Predicate>(
    (left, right) => ({ op: "and", left, right }),
    first,
  )
}

const overlapOf = (
  policy: Overlap["policy"],
  bound: number | undefined,
): Overlap => ({
  policy,
  bound: bound ?? 16,
})

export const desugarOnMessage = (
  options: unknown,
  handlerId: string,
): Rule => {
  const value = unwrap(decodeOnMessageOptions(options ?? {}))
  const parts: Predicate[] = [{ op: "kind", kind: "message" }]
  const channels = channelsOf(value.channel)
  if (channels !== undefined) {
    parts.push({ op: "channel", channels: [...channels] })
  }
  if (value.kind !== undefined) {
    parts.push({ op: "chat_kind", chat_kind: value.kind })
  }
  return {
    predicate: andAll(parts),
    overlap: overlapOf(value.overlap ?? "queue", value.bound),
    handler_id: handlerId,
  }
}

export const desugarOnAction = (
  options: unknown,
  handlerId: string,
): Rule => {
  const value = unwrap(decodeOnActionOptions(options ?? {}))
  const parts: Predicate[] = [{ op: "kind", kind: "action" }]
  const channels = channelsOf(value.channel)
  if (channels !== undefined) {
    parts.push({ op: "channel", channels: [...channels] })
  }
  return {
    predicate: andAll(parts),
    overlap: overlapOf(value.overlap ?? "drop", value.bound),
    handler_id: handlerId,
  }
}
```

`unwrap` throws the `DecodeError` value on a bad options object. Do not swallow it into a string.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/typescript && bun test test/desugar.test.ts`

Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add vectors/desugar_vectors.json \
  packages/typescript/src/facade/options.ts \
  packages/typescript/src/facade/desugar.ts \
  packages/typescript/test/desugar.test.ts
git commit -m "feat(ts): desugar onMessage/onAction options into A Rules"
```

---

### Task 2: Thread command collector

**Files:**
- Create: `packages/typescript/src/facade/thread.ts`
- Create: `packages/typescript/test/thread.test.ts`

**Interfaces:**
- Consumes: `Command`, `PostAction`, `ThreadId` from core
- Produces: `Thread`, `CommandSink`, `makeThread(id, sink) → Thread`

- [ ] **Step 1: Write the failing test**

Create `packages/typescript/test/thread.test.ts`:

```ts
import { expect, test } from "bun:test"
import * as Schema from "effect/Schema"
import type { Command } from "../src/core/index.ts"
import { ThreadId } from "../src/core/ids.ts"
import { makeThread } from "../src/facade/thread.ts"

const id = Schema.decodeUnknownSync(ThreadId)("telegram:1")

test("thread methods enqueue Commands and do not fetch", async () => {
  const commands: Command[] = []
  const thread = makeThread(id, (command) => {
    commands.push(command)
  })

  await thread.typing()
  await thread.post("hi", { actions: [{ text: "ok", data: "ok" }] })
  await thread.edit("m1", "later")
  await thread.react("m1", "👍")

  expect(thread.id).toBe(id)
  expect(commands).toEqual([
    { tag: "Typing", thread_id: id },
    {
      tag: "Post",
      thread_id: id,
      text: "hi",
      actions: [{ text: "ok", data: "ok" }],
    },
    { tag: "Edit", thread_id: id, message_id: "m1", text: "later" },
    { tag: "React", thread_id: id, message_id: "m1", emoji: "👍" },
  ])
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/typescript && bun test test/thread.test.ts`

Expected: FAIL — `src/facade/thread.ts` cannot be resolved.

- [ ] **Step 3: Implement Thread**

Create `packages/typescript/src/facade/thread.ts`:

```ts
import type { Command, PostAction } from "../core/commands.ts"
import type { ThreadId } from "../core/ids.ts"

export type CommandSink = (command: Command) => void

export type Thread = {
  readonly id: ThreadId
  post(
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  typing(): Promise<void>
  edit(messageId: string, text: string): Promise<void>
  react(messageId: string, emoji: string): Promise<void>
}

export const makeThread = (id: ThreadId, sink: CommandSink): Thread => ({
  id,
  post: async (text, options) => {
    sink({
      tag: "Post",
      thread_id: id,
      text,
      actions: options?.actions === undefined ? [] : [...options.actions],
    })
  },
  typing: async () => {
    sink({ tag: "Typing", thread_id: id })
  },
  edit: async (messageId, text) => {
    sink({ tag: "Edit", thread_id: id, message_id: messageId, text })
  },
  react: async (messageId, emoji) => {
    sink({ tag: "React", thread_id: id, message_id: messageId, emoji })
  },
})
```

Do not import `fetch`, `node:*`, or any adapter. `post` with no actions must still set `actions: []` so it matches the Command Schema default.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/typescript && bun test test/thread.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/src/facade/thread.ts packages/typescript/test/thread.test.ts
git commit -m "feat(ts): collect Thread.post/typing/edit/react as Commands"
```

---

### Task 3: B HostPort layer

**Files:**
- Create: `packages/typescript/src/facade/host.ts`
- Modify: `packages/typescript/test/facade.test.ts` (create; HostPort cases only in this task)

**Interfaces:**
- Consumes: `HostPort`, `HostContext` from `src/core/ports.ts`; `HostError` from `src/core/errors.ts`; `Event`, `Command`; `makeThread`
- Produces: `BHandler`, `bHostLayer(handlers: ReadonlyMap<string, BHandler>) → Layer<HostPort>`

A `BHandler` is `(thread, event, ctx) => void | Promise<void>`. The Layer:

1. Looks up `handlerId`. Missing → `HostError`.
2. If the stored kind is `message` and `event.kind !== "message"` (or action mismatch) → `HostError`.
3. Builds `makeThread(event.thread_id, sink)`.
4. `Effect.tryPromise` the handler. Throw → `HostError`.
5. Returns collected commands.

Keep Phase 3 `memoryHostLayer` / sync `HostFn` unchanged.

- [ ] **Step 1: Write the failing tests**

Create `packages/typescript/test/facade.test.ts` with only the HostPort cases for now:

```ts
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Schema from "effect/Schema"
import { Event, HostPort } from "../src/core/index.ts"
import { bHostLayer, type BHandler } from "../src/facade/host.ts"

const syncEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

const dm = Schema.decodeUnknownSync(Event)({
  kind: "message",
  thread_id: "telegram:1",
  text: "hello",
  chat_kind: "dm",
  sender: "u",
  raw: {},
})

test("bHostLayer runs a handler and returns Post", async () => {
  const handlers = new Map<string, BHandler>([
    [
      "onMessage:0",
      async (thread, event) => {
        const text = event.kind === "message" ? event.text : ""
        await thread.post(`echo:${text}`)
      },
    ],
  ])
  const effect = HostPort.pipe(
    Effect.flatMap((port) => port.run("onMessage:0", dm, { skipped: [] })),
    Effect.provide(bHostLayer(handlers)),
  )
  const commands = await Effect.runPromise(effect)
  expect(commands).toHaveLength(1)
  expect(commands[0]?.tag).toBe("Post")
  if (commands[0]?.tag === "Post") {
    expect(commands[0].text).toBe("echo:hello")
  }
})

test("bHostLayer missing handler is HostError", () => {
  const effect = HostPort.pipe(
    Effect.flatMap((port) => port.run("missing", dm, { skipped: [] })),
    Effect.provide(bHostLayer(new Map())),
  )
  const result = syncEither(effect)
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("HostError")
})
```

If `HostPort` is not re-exported from `src/core/index.ts`, import it from `src/core/ports.ts` instead. It is already exported via `export * from "./ports.ts"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/typescript && bun test test/facade.test.ts`

Expected: FAIL — `src/facade/host.ts` cannot be resolved.

- [ ] **Step 3: Implement bHostLayer**

Create `packages/typescript/src/facade/host.ts`:

```ts
import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Command } from "../core/commands.ts"
import { HostError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { HostPort, type HostContext } from "../core/ports.ts"
import { makeThread, type Thread } from "./thread.ts"

export type BHandler = (
  thread: Thread,
  event: Event,
  ctx: HostContext,
) => void | Promise<void>

export const bHostLayer = (
  handlers: ReadonlyMap<string, BHandler>,
): Layer.Layer<HostPort> =>
  Layer.succeed(HostPort, {
    run: (handlerId, event, ctx) =>
      Effect.gen(function* () {
        const fn = handlers.get(handlerId)
        if (fn === undefined) {
          return yield* Effect.fail(
            new HostError({
              reason: `no handler registered for ${handlerId}`,
              handlerId,
            }),
          )
        }
        const collected: Command[] = []
        const thread = makeThread(event.thread_id, (command) => {
          collected.push(command)
        })
        yield* Effect.tryPromise({
          try: () => Promise.resolve(fn(thread, event, ctx)),
          catch: (cause) =>
            new HostError({
              reason: cause instanceof Error ? cause.message : String(cause),
              handlerId,
            }),
        })
        return collected
      }),
  })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/typescript && bun test test/facade.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/src/facade/host.ts packages/typescript/test/facade.test.ts
git commit -m "feat(ts): run B handlers through HostPort and collect Commands"
```

---

### Task 4: Caspian class, interpret, e2e

**Files:**
- Create: `packages/typescript/src/facade/caspian.ts`
- Create: `packages/typescript/src/facade/index.ts`
- Modify: `packages/typescript/test/facade.test.ts`

**Interfaces:**
- Consumes: `desugarOnMessage`, `desugarOnAction`, `bHostLayer`, `makeMemoryInterpreter`, `App`, `Event`, `Predicate`, `Overlap`
- Produces: `Caspian` with `onMessage`, `onAction`, `use`, `program`, `interpret`

`interpret({ channel })` must call `makeMemoryInterpreter(this.program, { channelName, host: bHostLayer(this.handlers) })` and `Effect.runPromise` it.

Overload detection: if the first argument is a function, treat it as the handler and options as `{}`.

`use({ predicate, overlap? }, handler)` appends a `Rule` with `handler_id: use:N`. Default overlap `{ policy: "queue", bound: 16 }`.

- [ ] **Step 1: Write the failing Caspian tests (append to facade.test.ts)**

```ts
import { Caspian } from "../src/facade/caspian.ts"
import { makeMemoryInterpreter } from "../src/interpreters/memory.ts"

test("onMessage desugars into program.rules", () => {
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram", kind: "dm" }, async () => undefined)
  expect(cx.program.rules).toHaveLength(1)
  expect(cx.program.rules[0]?.handler_id).toBe("onMessage:0")
  expect(cx.program.rules[0]?.overlap).toEqual({ policy: "queue", bound: 16 })
})

test("onMessage without options still creates a Rule", () => {
  const cx = new Caspian()
  cx.onMessage(async () => undefined)
  expect(cx.program.rules[0]?.predicate).toEqual({ op: "kind", kind: "message" })
})

test("interpret feeds a DM and records Post", async () => {
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram", kind: "dm" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  const mem = await cx.interpret({ channel: "telegram" })
  const result = await Effect.runPromise(mem.run(dm))
  expect(result.decision).toBe("execute")
  const posts = await Effect.runPromise(mem.posts)
  expect(posts).toHaveLength(1)
  if (posts[0]?.tag === "Post") {
    expect(posts[0].text).toBe("echo:hello")
  }
})

test("channel filter does not run Host on the wrong channel", async () => {
  const cx = new Caspian()
  cx.onMessage({ channel: "discord" }, async (thread) => {
    await thread.post("nope")
  })
  const mem = await cx.interpret({ channel: "telegram" })
  const result = await Effect.runPromise(mem.run(dm))
  expect(result.decision).toBe("unmatched")
  const posts = await Effect.runPromise(mem.posts)
  expect(posts).toHaveLength(0)
})

test("handler throw is HostError, not a throw from step", async () => {
  const cx = new Caspian()
  cx.onMessage(async () => {
    throw new Error("boom")
  })
  const mem = await cx.interpret({ channel: "telegram" })
  await Effect.runPromise(mem.run(dm))
  const errors = await Effect.runPromise(mem.errors)
  expect(errors[0]?.reason).toContain("boom")
})
```

Remove the unused `makeMemoryInterpreter` import if `interpret` is the only entry. Do not construct Memory by hand in these tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/typescript && bun test test/facade.test.ts`

Expected: FAIL — `Caspian` cannot be resolved.

- [ ] **Step 3: Implement Caspian**

Create `packages/typescript/src/facade/caspian.ts`:

```ts
import type { App, Overlap, Rule } from "../core/app.ts"
import type { Event } from "../core/events.ts"
import type { Predicate } from "../core/predicates.ts"
import {
  makeMemoryInterpreter,
  type MemoryInterpreter,
} from "../interpreters/memory.ts"
import { desugarOnAction, desugarOnMessage } from "./desugar.ts"
import { bHostLayer, type BHandler } from "./host.ts"
import type { OnActionOptions, OnMessageOptions } from "./options.ts"
import * as Effect from "effect/Effect"

export class Caspian {
  readonly #rules: Rule[] = []
  readonly #handlers = new Map<string, BHandler>()
  #messageCount = 0
  #actionCount = 0
  #useCount = 0

  get program(): App {
    return { rules: [...this.#rules] }
  }

  onMessage(handler: BHandler): this
  onMessage(options: OnMessageOptions, handler: BHandler): this
  onMessage(
    optionsOrHandler: OnMessageOptions | BHandler,
    maybeHandler?: BHandler,
  ): this {
    const { options, handler } = split(optionsOrHandler, maybeHandler)
    const handlerId = `onMessage:${this.#messageCount}`
    this.#messageCount += 1
    this.#rules.push(desugarOnMessage(options, handlerId))
    this.#handlers.set(handlerId, handler)
    return this
  }

  onAction(handler: BHandler): this
  onAction(options: OnActionOptions, handler: BHandler): this
  onAction(
    optionsOrHandler: OnActionOptions | BHandler,
    maybeHandler?: BHandler,
  ): this {
    const { options, handler } = split(optionsOrHandler, maybeHandler)
    const handlerId = `onAction:${this.#actionCount}`
    this.#actionCount += 1
    this.#rules.push(desugarOnAction(options, handlerId))
    this.#handlers.set(handlerId, handler)
    return this
  }

  use(
    input: { readonly predicate: Predicate; readonly overlap?: Overlap },
    handler: BHandler,
  ): this {
    const handlerId = `use:${this.#useCount}`
    this.#useCount += 1
    this.#rules.push({
      predicate: input.predicate,
      overlap: input.overlap ?? { policy: "queue", bound: 16 },
      handler_id: handlerId,
    })
    this.#handlers.set(handlerId, handler)
    return this
  }

  interpret(options: { readonly channel?: string } = {}): Promise<MemoryInterpreter> {
    return Effect.runPromise(
      makeMemoryInterpreter(this.program, {
        channelName: options.channel ?? "",
        host: bHostLayer(this.#handlers),
      }),
    )
  }
}

const split = (
  optionsOrHandler: object | BHandler,
  maybeHandler: BHandler | undefined,
): { options: unknown; handler: BHandler } => {
  if (typeof optionsOrHandler === "function") {
    return { options: {}, handler: optionsOrHandler }
  }
  if (maybeHandler === undefined) {
    throw new TypeError("handler is required")
  }
  return { options: optionsOrHandler, handler: maybeHandler }
}
```

Narrow `onMessage` handlers so `msg` is `Message` in user code: wrap the stored `BHandler` with a kind check, or type the public signatures as `MessageHandler` / `ActionHandler` (see spec) and convert to `BHandler` at the map. Public `.d.ts` must show `(thread, message: Message, ctx)` for `onMessage`. If a wrapper is needed:

```ts
const asMessageHandler = (fn: MessageHandler): BHandler =>
  async (thread, event, ctx) => {
    if (event.kind !== "message") {
      throw new Error(`onMessage handler received ${event.kind}`)
    }
    await fn(thread, event, ctx)
  }
```

Put `MessageHandler` / `ActionHandler` in `src/facade/host.ts` or `caspian.ts` and export them.

Create `packages/typescript/src/facade/index.ts`:

```ts
export { Caspian } from "./caspian.ts"
export type { ActionHandler, BHandler, MessageHandler } from "./host.ts"
export type { OnActionOptions, OnMessageOptions } from "./options.ts"
export type { Thread } from "./thread.ts"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/typescript && bun test test/facade.test.ts test/desugar.test.ts test/thread.test.ts`

Expected: PASS. Then `cd packages/typescript && bun run ci` — all previous 31 plus new tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/src/facade/caspian.ts \
  packages/typescript/src/facade/index.ts \
  packages/typescript/test/facade.test.ts
git commit -m "feat(ts): add Caspian onMessage/onAction facade over Memory"
```

---

### Task 5: Public barrel, README, facade boundary

**Files:**
- Modify: `packages/typescript/src/index.ts`
- Modify: `packages/typescript/README.md`
- Modify: `packages/typescript/test/core-boundaries.test.ts` (optional extra assertion)
- Modify: `packages/typescript/eslint.config.js` only if `onMessage` handlers need a facade-restricted import rule — skip unless a test requires it

**Interfaces:**
- Consumes: `Caspian` and public types from `src/facade`
- Produces: `import { Caspian } from "caspian"` works; README teaches `onMessage` first

- [ ] **Step 1: Write the failing public-export test**

Append to `packages/typescript/test/facade.test.ts`:

```ts
import { Caspian as PublicCaspian } from "../src/index.ts"

test("package root exports Caspian", () => {
  expect(new PublicCaspian().program.rules).toEqual([])
})
```

- [ ] **Step 2: Run it to verify the current barrel fails**

Run: `cd packages/typescript && bun test test/facade.test.ts`

Expected: FAIL — `src/index.ts` exports `{}` only, or `Caspian` is not exported.

- [ ] **Step 3: Export the facade and rewrite the README**

Replace `packages/typescript/src/index.ts` with:

```ts
/**
 * Caspian TypeScript SDK (rewrite).
 *
 * App code imports this barrel. Do not import src/core from application code.
 */
export { Caspian } from "./facade/caspian.ts"
export type {
  ActionHandler,
  MessageHandler,
} from "./facade/host.ts"
export type { OnActionOptions, OnMessageOptions } from "./facade/options.ts"
export type { Thread } from "./facade/thread.ts"
export type { Action, Command, Event, Message } from "./core/index.ts"
```

Replace `packages/typescript/README.md` with:

```md
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
```

Do not mention `cx.use`, `on(message & ~dm())`, or `src/core` in the README.

- [ ] **Step 4: Run the full gate**

Run: `cd packages/typescript && bun run ci`

Expected: typecheck, eslint, depcruise, and all tests pass. Core still cannot import `facade`. Count is previous 31 plus the new desugar / thread / facade tests.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/src/index.ts \
  packages/typescript/README.md \
  packages/typescript/test/facade.test.ts
git commit -m "feat(ts): export Caspian as the public SDK surface"
```

---

## Self-review

**Spec coverage**

| Spec section | Task |
|---|---|
| Desugar law / golden App | Task 1 |
| `kind` is chat_kind | Task 1 vectors + reject `kind: "text"` |
| Extra option keys | Task 1 |
| Thread Command law | Task 2 |
| HostPort runs `fn`, `step` does not | Task 3 |
| `onMessage` / `onAction` / `use` / `interpret` | Task 4 |
| Channel filter e2e | Task 4 |
| Handler throw → `HostError` | Task 4 |
| Public barrel + README | Task 5 |
| No adapters / provision / tools | Out of scope; no task adds them |

**Placeholder scan:** none. Task 1 ships `unwrap` in the same step as desugar.

**Type consistency:** `BHandler`, `Thread`, `OnMessageOptions`, `desugarOnMessage(options, handlerId)`, `bHostLayer`, `Caspian.interpret`, `program: App` are the same names in the spec and every task.
