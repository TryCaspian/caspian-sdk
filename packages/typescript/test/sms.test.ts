import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  overlapKey,
  parseSmsUpdate,
  planCommand,
  sms,
  verifySms,
} from "../src/adapters/sms.ts"
import {
  AdapterError,
  Connection,
  ConnectionId,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "sms",
  config: {
    accountSid: "AC123",
    authToken: "tok",
    fromNumber: "+15559876543",
  },
})

test("parse form inbound", () => {
  const body = new URLSearchParams({
    From: "+15551234567",
    To: "+15559876543",
    Body: "hello there",
    MessageSid: "SM123",
  }).toString()
  const events = Effect.runSync(parseSmsUpdate(body))
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hello there")
  expect(String(event.thread_id)).toBe("sms:+15551234567")
  expect(event.sender).toBe("+15551234567")
  expect(event.chat_kind).toBe("dm")
})

test("parse no From returns empty", () => {
  const body = new URLSearchParams({ Body: "orphan" }).toString()
  expect(Effect.runSync(parseSmsUpdate(body))).toEqual([])
})

test("plan Post Messages API", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "sms:+15551234567",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_form")
  if (planned?.transport !== "http_form") {
    return
  }
  expect(planned.url).toContain("Messages.json")
  expect(planned.form).toEqual({
    To: "+15551234567",
    From: "+15559876543",
    Body: "hi",
  })
  expect(planned.headers?.["Authorization"]?.startsWith("Basic ")).toBe(true)
})

test("plan without creds is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "sms",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "sms:+15551234567",
      text: "hi",
      actions: [],
    }),
  )
  const result = Effect.runSync(Effect.either(planCommand(post, empty)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(AdapterError)
})

test("plan React is AdapterError", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "sms:+15551234567",
      message_id: "SM1",
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

test("overlap verify capabilities", () => {
  const events = Effect.runSync(
    parseSmsUpdate(
      new URLSearchParams({ From: "+15551234567", Body: "hi" }).toString(),
    ),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("sms:+15551234567")
  expect(sms().capabilities()).toEqual(["receive", "reply", "send", "media"])
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "sms",
    config: {},
  })
  expect(verifySms("From=%2B1", {}, empty)).toBe(true)
})
