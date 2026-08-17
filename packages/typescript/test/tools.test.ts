import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Schema from "effect/Schema"
import type { Command } from "../src/core/index.ts"
import { ThreadId } from "../src/core/ids.ts"
import { Caspian } from "../src/facade/caspian.ts"
import { makeThread } from "../src/facade/thread.ts"

const id = Schema.decodeUnknownSync(ThreadId)("telegram:123")

const threadWith = (commands: Command[]) =>
  makeThread(id, (command) => {
    commands.push(command)
  })

const namesOf = (tools: Record<string, { readonly name: string }>) =>
  Object.keys(tools).sort()

test("messenger tools are the Command send surface plus send_dm", () => {
  const cx = new Caspian()
  const tools = cx.tools(threadWith([]), { preset: "messenger" })
  expect(namesOf(tools)).toEqual([
    "add_reaction",
    "edit_message",
    "post_message",
    "send_dm",
    "start_typing",
  ])
})

test("outbound preset is post_message and send_dm", () => {
  const cx = new Caspian()
  const tools = cx.tools({ preset: "outbound" })
  expect(namesOf(tools)).toEqual(["post_message", "send_dm"])
})

test("tools do not expose Host or Call", () => {
  const cx = new Caspian()
  const tools = cx.tools(threadWith([]), { preset: "messenger" })
  expect("host" in tools).toBe(false)
  expect("call" in tools).toBe(false)
  expect(tools.post_message.name).toBe("post_message")
})

test("bound post_message enqueues Post on the thread and does not take chat_id", async () => {
  const commands: Command[] = []
  const cx = new Caspian()
  const tools = cx.tools(threadWith(commands), { preset: "messenger" })
  expect(tools.post_message?.parameters).not.toHaveProperty("chat_id")
  expect(
    (tools.post_message?.parameters as { properties?: object }).properties,
  ).not.toHaveProperty("chat_id")
  const result = await tools.post_message.execute({ text: "hi" })
  expect(result.tag).toBe("Post")
  expect(commands).toEqual([
    { tag: "Post", thread_id: id, text: "hi", actions: [] },
  ])
})

test("outbound post_message requires thread_id and does not bind a chat", async () => {
  const cx = new Caspian()
  const tools = cx.tools({ preset: "outbound" })
  const result = await tools.post_message.execute({
    thread_id: "telegram:9",
    text: "ping",
  })
  expect(result).toEqual({
    tag: "Post",
    thread_id: Schema.decodeUnknownSync(ThreadId)("telegram:9"),
    text: "ping",
    actions: [],
  })
})

test("send_dm enqueues Post to the named thread, not the bound thread", async () => {
  const commands: Command[] = []
  const cx = new Caspian()
  const tools = cx.tools(threadWith(commands), { preset: "messenger" })
  await tools.send_dm.execute({ thread_id: "telegram:dm", text: "secret" })
  expect(commands).toEqual([
    {
      tag: "Post",
      thread_id: Schema.decodeUnknownSync(ThreadId)("telegram:dm"),
      text: "secret",
      actions: [],
    },
  ])
})

test("bad tool args are DecodeError", async () => {
  const cx = new Caspian()
  const tools = cx.tools({ preset: "outbound" })
  const result = await Effect.runPromise(
    Effect.either(
      Effect.tryPromise({
        try: () => tools.post_message.execute({ text: "no thread" }),
        catch: (error) => error as { readonly _tag?: string },
      }),
    ),
  )
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("DecodeError")
})
