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
