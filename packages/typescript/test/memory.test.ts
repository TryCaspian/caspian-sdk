import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import { App, Event } from "../src/core/index.ts"
import {
  chaosHostLayer,
  makeMemoryInterpreter,
  type HostFn,
  type MemoryInterpreter,
  type MemoryInterpreterOptions,
} from "../src/interpreters/memory.ts"

const sync = <A>(effect: Effect.Effect<A, never>) => Effect.runSync(effect)

const messageApp = (overlap: "queue" | "drop" | "debounce" | "parallel") =>
  Schema.decodeUnknownSync(App)({
    rules: [
      {
        predicate: { op: "kind", kind: "message" },
        overlap: { policy: overlap, bound: 16 },
        handler_id: "h1",
      },
    ],
  })

const dm = (text: string, thread = "telegram:1"): Event =>
  Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: thread,
    text,
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })

const boot = (
  overlap: "queue" | "drop" | "debounce" | "parallel",
  fn?: HostFn,
  host?: MemoryInterpreterOptions["host"],
): MemoryInterpreter => {
  const mem = sync(
    makeMemoryInterpreter(messageApp(overlap), {
      channelName: "telegram",
      ...(host === undefined ? {} : { host }),
    }),
  )
  if (fn) {
    sync(mem.register("h1", fn))
  }
  return mem
}

test("a fixture DM records Post with no network", () => {
  const interp = boot("queue", (event) => [
    {
      tag: "Post",
      thread_id: event.thread_id,
      text: `echo:${event.kind === "message" ? event.text : ""}`,
      actions: [],
    },
  ])

  const result = sync(interp.run(dm("hello")))
  expect(result.decision).toBe("execute")
  const posts = sync(interp.posts)
  expect(posts).toHaveLength(1)
  const post = posts[0]
  expect(post?.tag).toBe("Post")
  if (post?.tag === "Post") {
    expect(post.text).toBe("echo:hello")
  }
})

test("queue burst runs the first then the latest with skipped", () => {
  const seen: Array<{ text: string; skipped: ReadonlyArray<string> }> = []
  const interp = boot("queue", (event, ctx) => {
    seen.push({
      text: event.kind === "message" ? event.text : "",
      skipped: ctx.skipped.map((item) =>
        item.kind === "message" ? item.text : "",
      ),
    })
    return [{ tag: "Post", thread_id: event.thread_id, text: "ok", actions: [] }]
  })

  const results = sync(interp.runSequence([dm("one"), dm("two"), dm("three")]))
  expect(results.map((item) => item.decision)).toEqual([
    "execute",
    "enqueue",
    "enqueue",
  ])
  expect(seen).toEqual([
    { text: "one", skipped: [] },
    { text: "three", skipped: ["two"] },
  ])
  expect(sync(interp.posts)).toHaveLength(2)
})

test("drop discards the overlapping event", () => {
  const seen: Array<string> = []
  const interp = boot("drop", (event) => {
    seen.push(event.kind === "message" ? event.text : "")
    return []
  })

  const results = sync(interp.runSequence([dm("one"), dm("two")]))
  expect(results.map((item) => item.decision)).toEqual(["execute", "drop"])
  expect(seen).toEqual(["one"])
})

test("debounce Queue.sliding(1) keeps only the latest waiter", () => {
  const seen: Array<{ text: string; skippedCount: number }> = []
  const interp = boot("debounce", (event, ctx) => {
    seen.push({
      text: event.kind === "message" ? event.text : "",
      skippedCount: ctx.skipped.length,
    })
    return []
  })

  sync(interp.runSequence([dm("one"), dm("two"), dm("three")]))
  expect(seen).toEqual([
    { text: "one", skippedCount: 0 },
    { text: "three", skippedCount: 0 },
  ])
})

test("chaos host layer records a HostError instead of posting", () => {
  const interp = boot("queue", undefined, chaosHostLayer)
  sync(interp.run(dm("hello")))
  expect(sync(interp.posts)).toHaveLength(0)
  expect(sync(interp.errors)).toHaveLength(1)
  expect(sync(interp.errors)[0]?._tag).toBe("HostError")
})
