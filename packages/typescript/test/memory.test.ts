import { expect, test } from "bun:test"
import * as Schema from "effect/Schema"
import { App, Event } from "../src/core/index.ts"
import {
  MemoryInterpreter,
  chaosHostLayer,
} from "../src/interpreters/memory.ts"

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

test("a fixture DM records Post with no network", () => {
  const interp = new MemoryInterpreter(messageApp("queue"), {
    channelName: "telegram",
  })
  interp.register("h1", (event) => [
    {
      tag: "Post",
      thread_id: event.thread_id,
      text: `echo:${event.kind === "message" ? event.text : ""}`,
      actions: [],
    },
  ])

  const result = interp.run(dm("hello"))
  expect(result.decision).toBe("execute")
  expect(interp.posts()).toHaveLength(1)
  const post = interp.posts()[0]
  expect(post?.tag).toBe("Post")
  if (post?.tag === "Post") {
    expect(post.text).toBe("echo:hello")
  }
})

test("queue burst runs the first then the latest with skipped", () => {
  const seen: Array<{ text: string; skipped: ReadonlyArray<string> }> = []
  const interp = new MemoryInterpreter(messageApp("queue"), {
    channelName: "telegram",
  })
  interp.register("h1", (event, ctx) => {
    seen.push({
      text: event.kind === "message" ? event.text : "",
      skipped: ctx.skipped.map((item) => (item.kind === "message" ? item.text : "")),
    })
    return [{ tag: "Post", thread_id: event.thread_id, text: "ok", actions: [] }]
  })

  const results = interp.runSequence([dm("one"), dm("two"), dm("three")])
  expect(results.map((item) => item.decision)).toEqual([
    "execute",
    "enqueue",
    "enqueue",
  ])
  expect(seen).toEqual([
    { text: "one", skipped: [] },
    { text: "three", skipped: ["two"] },
  ])
  expect(interp.posts()).toHaveLength(2)
})

test("drop discards the overlapping event", () => {
  const seen: Array<string> = []
  const interp = new MemoryInterpreter(messageApp("drop"), {
    channelName: "telegram",
  })
  interp.register("h1", (event) => {
    seen.push(event.kind === "message" ? event.text : "")
    return []
  })

  const results = interp.runSequence([dm("one"), dm("two")])
  expect(results.map((item) => item.decision)).toEqual(["execute", "drop"])
  expect(seen).toEqual(["one"])
})

test("debounce keeps only the latest waiting event", () => {
  const seen: Array<{ text: string; skippedCount: number }> = []
  const interp = new MemoryInterpreter(messageApp("debounce"), {
    channelName: "telegram",
  })
  interp.register("h1", (event, ctx) => {
    seen.push({
      text: event.kind === "message" ? event.text : "",
      skippedCount: ctx.skipped.length,
    })
    return []
  })

  interp.runSequence([dm("one"), dm("two"), dm("three")])
  expect(seen).toEqual([
    { text: "one", skippedCount: 0 },
    { text: "three", skippedCount: 0 },
  ])
})

test("chaos host layer records a HostError instead of posting", () => {
  const interp = new MemoryInterpreter(messageApp("queue"), {
    channelName: "telegram",
    host: chaosHostLayer,
  })
  interp.run(dm("hello"))
  expect(interp.posts()).toHaveLength(0)
  expect(interp.errors).toHaveLength(1)
  expect(interp.errors[0]?._tag).toBe("HostError")
})
