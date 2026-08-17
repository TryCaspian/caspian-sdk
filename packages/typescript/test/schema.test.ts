import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Schema from "effect/Schema"
import {
  App,
  CaspianError,
  Command,
  Event,
  decodeApp,
  decodeCommand,
  decodeEvent,
} from "../src/core/index.ts"

const runEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

test("decodes a Message event into branded thread_id fields", () => {
  const result = runEither(
    decodeEvent({
      kind: "message",
      thread_id: "telegram:123",
      text: "hello",
      chat_kind: "dm",
      sender: "user1",
      raw: {},
    }),
  )

  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.kind).toBe("message")
  expect(String(result.right.thread_id)).toBe("telegram:123")
  if (result.right.kind !== "message") {
    throw new Error("expected message")
  }
  expect(result.right.chat_kind).toBe("dm")
  expect(result.right.text).toBe("hello")
})

test("rejects extra fields on Message (parse, don't validate)", () => {
  const result = runEither(
    decodeEvent({
      kind: "message",
      thread_id: "telegram:123",
      text: "hello",
      chat_kind: "dm",
      extra: true,
    }),
  )

  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("DecodeError")
})

test("rejects an unknown event kind", () => {
  const result = runEither(
    decodeEvent({
      kind: "sticker",
      thread_id: "telegram:123",
    }),
  )

  expect(Either.isLeft(result)).toBe(true)
})

test("round-trips Command JSON used in golden vectors", () => {
  const typing = { tag: "Typing" as const, thread_id: "telegram:123" }
  const host = { tag: "Host" as const, handler_id: "h1" }

  const decodedTyping = runEither(decodeCommand(typing))
  const decodedHost = runEither(decodeCommand(host))
  expect(Either.isRight(decodedTyping)).toBe(true)
  expect(Either.isRight(decodedHost)).toBe(true)
  if (Either.isLeft(decodedTyping) || Either.isLeft(decodedHost)) {
    return
  }

  expect(Schema.encodeSync(Command)(decodedTyping.right)).toEqual(typing)
  expect(Schema.encodeSync(Command)(decodedHost.right)).toEqual(host)
})

test("rejects extra fields on Post", () => {
  const result = runEither(
    decodeCommand({
      tag: "Post",
      thread_id: "telegram:1",
      text: "hi",
      http: true,
    }),
  )
  expect(Either.isLeft(result)).toBe(true)
})

test("decodes nested And/channel predicates from vector JSON", () => {
  const result = runEither(
    decodeApp({
      rules: [
        {
          predicate: {
            op: "and",
            left: { op: "kind", kind: "message" },
            right: { op: "channel", channels: ["discord"] },
          },
          overlap: { policy: "queue", bound: 16 },
          handler_id: "h3",
        },
      ],
    }),
  )

  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  const rule = result.right.rules[0]
  expect(rule?.handler_id).toBe("h3")
  expect(rule?.overlap.policy).toBe("queue")
  expect(rule?.predicate.op).toBe("and")
})

test("rejects extra fields on App", () => {
  const result = runEither(
    decodeApp({
      rules: [],
      secret: "nope",
    }),
  )
  expect(Either.isLeft(result)).toBe(true)
})

test("rejects a non-positive overlap bound", () => {
  const result = runEither(
    decodeApp({
      rules: [
        {
          predicate: { op: "kind", kind: "message" },
          overlap: { policy: "queue", bound: 0 },
          handler_id: "h",
        },
      ],
    }),
  )
  expect(Either.isLeft(result)).toBe(true)
})

test("Event and App schemas are the vector round-trip codecs", () => {
  expect(Event.ast._tag).toBe("Union")
  expect(App.ast._tag).toBe("TypeLiteral")
})

test("CaspianError has no OverlapFull arm — queue-at-bound is a drop, not an error", () => {
  const result = Schema.decodeUnknownEither(CaspianError)({
    _tag: "OverlapFull",
    threadId: "telegram:1",
    bound: 16,
  })
  expect(Either.isLeft(result)).toBe(true)
})
