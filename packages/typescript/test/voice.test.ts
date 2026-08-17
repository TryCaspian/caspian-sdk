import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  overlapKey,
  parseVoiceUpdate,
  planCommand,
  voice,
} from "../src/adapters/voice.ts"
import {
  AdapterError,
  Connection,
  ConnectionId,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "voice",
  config: {},
})

test("parse call form", () => {
  const body = new URLSearchParams({
    CallSid: "CA123",
    From: "+15551234567",
    To: "+15559876543",
    SpeechResult: "book a table",
  }).toString()
  const events = Effect.runSync(parseVoiceUpdate(body))
  expect(events).toHaveLength(1)
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("book a table")
  expect(String(event.thread_id)).toBe("voice:CA123")
  expect(event.sender).toBe("+15551234567")
  expect(event.chat_kind).toBe("dm")
})

test("parse no CallSid returns empty", () => {
  const body = new URLSearchParams({ From: "+15551234567" }).toString()
  expect(Effect.runSync(parseVoiceUpdate(body))).toEqual([])
})

test("plan Post produces TwiML Say", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "voice:CA123",
      text: "hello caller",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("twiml")
  if (planned?.transport !== "twiml") {
    return
  }
  expect(planned.twiml).toContain("<Say>hello caller</Say>")
  expect(planned.twiml.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(
    true,
  )
})

test("plan Post escapes XML", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "voice:CA123",
      text: "a & b < c",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  if (planned?.transport !== "twiml") {
    return
  }
  expect(planned.twiml).toContain("a &amp; b &lt; c")
})

test("plan React is AdapterError", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "voice:CA123",
      message_id: "CA1",
      emoji: "👍",
    }),
  )
  const result = Effect.runSync(Effect.either(planCommand(react, conn)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(AdapterError)
  expect(result.left.commandTag).toBe("React")
})

test("overlap key and capabilities", () => {
  const events = Effect.runSync(
    parseVoiceUpdate(new URLSearchParams({ CallSid: "CA123" }).toString()),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("voice:CA123")
  expect(voice().capabilities()).toEqual(["receive", "send", "voice", "tts"])
})
