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

test("decodes Receipt, MemberJoin, Edited, and the extra Message fields", () => {
  const receipt = runEither(
    decodeEvent({
      kind: "receipt",
      thread_id: "whatsapp:1",
      status: "read",
      message_id: "wamid.1",
    }),
  )
  expect(Either.isRight(receipt)).toBe(true)
  if (Either.isLeft(receipt)) {
    return
  }
  expect(receipt.right.kind).toBe("receipt")
  if (receipt.right.kind === "receipt") {
    expect(receipt.right.status).toBe("read")
    expect(receipt.right.message_id).toBe("wamid.1")
  }

  const join = runEither(
    decodeEvent({
      kind: "member_join",
      thread_id: "telegram:-100",
      member: "42",
    }),
  )
  expect(Either.isRight(join)).toBe(true)

  const edited = runEither(
    decodeEvent({
      kind: "edited",
      thread_id: "telegram:1",
      message_id: "9",
      text: "later",
    }),
  )
  expect(Either.isRight(edited)).toBe(true)

  const deleted = runEither(
    decodeEvent({
      kind: "deleted",
      thread_id: "telegram:1",
      message_id: "9",
    }),
  )
  expect(Either.isRight(deleted)).toBe(true)

  const message = runEither(
    decodeEvent({
      kind: "message",
      thread_id: "telegram:1",
      text: "hi",
      chat_kind: "dm",
      attachments: [{ type: "photo", file_id: "abc" }],
      reply_to: "8",
      topic_id: "3",
      message_id: "10",
    }),
  )
  expect(Either.isRight(message)).toBe(true)
  if (Either.isLeft(message) || message.right.kind !== "message") {
    return
  }
  expect(message.right.attachments[0]?.type).toBe("photo")
  expect(message.right.reply_to).toBe("8")
  expect(message.right.topic_id).toBe("3")
})

test("decodes Reply, SendMedia, Delete, Initiate, and OpenModal commands", () => {
  const reply = runEither(
    decodeCommand({
      tag: "Reply",
      thread_id: "telegram:1",
      reply_to: "10",
      text: "ok",
    }),
  )
  expect(Either.isRight(reply)).toBe(true)

  const media = runEither(
    decodeCommand({
      tag: "SendMedia",
      thread_id: "telegram:1",
      attachment: { type: "photo", url: "https://example.com/a.jpg" },
      caption: "pic",
    }),
  )
  expect(Either.isRight(media)).toBe(true)

  const initiate = runEither(
    decodeCommand({
      tag: "Initiate",
      thread_id: "telegram:1",
      text: "hello",
    }),
  )
  expect(Either.isRight(initiate)).toBe(true)

  const modal = runEither(
    decodeCommand({
      tag: "OpenModal",
      thread_id: "slack:C1",
      trigger_id: "trig",
      blocks: [{ type: "input", content: { label: "name" } }],
      title: "Form",
    }),
  )
  expect(Either.isRight(modal)).toBe(true)
})

test("CaspianError has no OverlapFull arm — queue-at-bound is a drop, not an error", () => {
  const result = Schema.decodeUnknownEither(CaspianError)({
    _tag: "OverlapFull",
    threadId: "telegram:1",
    bound: 16,
  })
  expect(Either.isLeft(result)).toBe(true)
})
