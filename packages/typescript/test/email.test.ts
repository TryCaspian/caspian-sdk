import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  encodeThreadId,
  overlapKey,
  parseEmailUpdate,
  planCommand,
  email,
} from "../src/adapters/email.ts"
import {
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "email",
  config: { fromAddress: "bot@caspian.dev", defaultSubject: "Support" },
})

test("parse simplified inbound", () => {
  const events = Effect.runSync(
    parseEmailUpdate({
      from: "Alice <Alice@Example.com>",
      to: "bot@caspian.dev",
      subject: "Hello",
      body: "hi there",
      message_id: "<abc@example.com>",
      in_reply_to: "<prev@example.com>",
    }),
  )
  expect(events).toHaveLength(1)
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hi there")
  expect(event.chat_kind).toBe("dm")
  expect(event.sender).toBe("alice@example.com")
  expect(String(event.thread_id)).toBe("email:alice@example.com")
})

test("parse SNS wrapped best-effort", () => {
  const content = [
    "From: Bob <bob@example.com>",
    "To: bot@caspian.dev",
    "Subject: Re: Ticket",
    "Message-ID: <m1@example.com>",
    "In-Reply-To: <orig@example.com>",
    "Content-Type: text/plain; charset=utf-8",
    "",
    "please help me",
  ].join("\r\n")
  const inner = {
    notificationType: "Received",
    mail: {
      source: "bob@example.com",
      destination: ["bot@caspian.dev"],
      messageId: "ses-123",
      commonHeaders: {
        from: ["Bob <bob@example.com>"],
        to: ["bot@caspian.dev"],
        subject: "Re: Ticket",
        messageId: "<m1@example.com>",
      },
    },
    content,
  }
  const events = Effect.runSync(
    parseEmailUpdate({ Type: "Notification", Message: JSON.stringify(inner) }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.sender).toBe("bob@example.com")
  expect(String(event.thread_id)).toBe("email:bob@example.com")
  expect(event.text).toBe("please help me")
})

test("unknown object returns empty", () => {
  expect(Effect.runSync(parseEmailUpdate({ foo: "bar" }))).toEqual([])
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseEmailUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post smtp sendmail", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "email:alice@example.com",
      text: "hello",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("smtp")
  if (planned?.transport !== "smtp") {
    return
  }
  expect(planned.native).toBe("sendmail")
  expect(planned.email.from).toBe("bot@caspian.dev")
  expect(planned.email.to).toBe("alice@example.com")
  expect(planned.email.subject).toBe("Support")
  expect(planned.email.body).toBe("hello")
})

test("plan Post default subject fallback", () => {
  const bare = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c2"),
    channel: "email",
    config: { fromAddress: "bot@caspian.dev" },
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "email:alice@example.com",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, bare))
  if (planned?.transport !== "smtp") {
    return
  }
  expect(planned.email.subject).toBe("(no subject)")
})

test("plan React is AdapterError", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "email:alice@example.com",
      message_id: "<m1@example.com>",
      emoji: "👍",
    }),
  )
  const result = Effect.runSync(Effect.either(planCommand(react, conn)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left.commandTag).toBe("React")
})

test("encode decode overlap capabilities", () => {
  const tid = encodeThreadId({ address: "Alice@Example.com" })
  expect(String(tid)).toBe("email:alice@example.com")
  const events = Effect.runSync(
    parseEmailUpdate({
      from: "alice@example.com",
      body: "hi",
      message_id: "1",
    }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("email:alice@example.com")
  expect(email().format("plain *text*")).toBe("plain *text*")
})
