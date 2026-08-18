import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Layer from "effect/Layer"
import * as Schema from "effect/Schema"
import { Event, HostPort } from "../src/core/index.ts"
import { Caspian } from "../src/facade/caspian.ts"
import { Caspian as PublicCaspian } from "../src/index.ts"
import {
  bHostLayer,
  emptyThreadStoreLayer,
  type BHandler,
} from "../src/facade/host.ts"

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
    Effect.provide(
      bHostLayer(handlers).pipe(Layer.provide(emptyThreadStoreLayer)),
    ),
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
    Effect.provide(
      bHostLayer(new Map()).pipe(Layer.provide(emptyThreadStoreLayer)),
    ),
  )
  const result = syncEither(effect)
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("HostError")
})

test("onMessage desugars into program.rules", () => {
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram", kind: "dm" }, async () => undefined)
  expect(cx.app.rules).toHaveLength(1)
  expect(cx.app.rules[0]?.handler_id).toBe("onMessage:0")
  expect(cx.app.rules[0]?.overlap).toEqual({ policy: "queue", bound: 16 })
})

test("onMessage without options still creates a Rule", () => {
  const cx = new Caspian()
  cx.onMessage(async () => undefined)
  expect(cx.app.rules[0]?.predicate).toEqual({ op: "kind", kind: "message" })
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

test("package root exports Caspian", () => {
  expect(new PublicCaspian().app.rules).toEqual([])
})

test("thread.recent is prior events on that thread, not the current one", async () => {
  const seen: Array<ReadonlyArray<string>> = []
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    const history = await thread.recent(20)
    seen.push(history.map((item) => (item.kind === "message" ? item.text : "")))
    await thread.post(`echo:${msg.text}`)
  })
  const mem = await cx.interpret({ channel: "telegram" })
  const first = Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: "telegram:1",
    text: "one",
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })
  const second = Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: "telegram:1",
    text: "two",
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })
  const other = Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: "telegram:9",
    text: "other",
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })
  await Effect.runPromise(mem.runSequence([first, other, second]))
  expect(seen).toEqual([[], [], ["one"]])
})

test("thread.state persists across turns on the runner", async () => {
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    if (msg.text === "save") {
      await thread.state.set("n", 1)
      expect(await thread.state.get("n")).toBe(1)
      return
    }
    expect(await thread.state.get("n")).toBe(1)
  })
  const mem = await cx.interpret({ channel: "telegram" })
  const save = Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: "telegram:1",
    text: "save",
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })
  const load = Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: "telegram:1",
    text: "load",
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })
  await Effect.runPromise(mem.runSequence([save, load]))
  const commands = await Effect.runPromise(mem.commands)
  expect(commands.some((command) => command.tag === "SetState")).toBe(true)
})
