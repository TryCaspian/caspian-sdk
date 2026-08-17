import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Schema from "effect/Schema"
import { Event, HostPort } from "../src/core/index.ts"
import { Caspian } from "../src/facade/caspian.ts"
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
