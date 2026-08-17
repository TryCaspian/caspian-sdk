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
