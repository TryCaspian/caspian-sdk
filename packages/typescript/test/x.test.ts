import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
  parseXUpdate,
  planCommand,
  verifyX,
  x,
} from "../src/adapters/x.ts"
import {
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "x",
  config: { bearerToken: "TOKEN" },
})

test("parse dm event", () => {
  const events = Effect.runSync(
    parseXUpdate({
      direct_message_events: [
        {
          id: "dm1",
          message_create: {
            sender_id: "12345",
            message_data: { text: "hey there" },
          },
        },
      ],
    }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hey there")
  expect(event.sender).toBe("12345")
  expect(event.chat_kind).toBe("dm")
  expect(String(event.thread_id)).toBe("x:dm:12345")
})

test("parse simple dm", () => {
  const events = Effect.runSync(parseXUpdate({ dm: { from: "999", text: "ping" } }))
  expect(events[0]?.kind).toBe("message")
  if (events[0]?.kind !== "message") {
    return
  }
  expect(events[0].chat_kind).toBe("dm")
  expect(String(events[0].thread_id)).toBe("x:dm:999")
})

test("parse tweet event", () => {
  const events = Effect.runSync(
    parseXUpdate({
      tweet_create_events: [{ id: "t1", text: "hello world", user: { id: "777" } }],
    }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hello world")
  expect(event.chat_kind).toBe("channel")
  expect(String(event.thread_id)).toBe("x:777")
})

test("unknown returns empty", () => {
  expect(Effect.runSync(parseXUpdate({ favorite_events: [{ id: "f1" }] }))).toEqual(
    [],
  )
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseXUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post tweet", () => {
  const post = Effect.runSync(
    decodeCommand({ tag: "Post", thread_id: "x:777", text: "gm", actions: [] }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("createTweet")
  expect(planned.url).toBe("https://api.twitter.com/2/tweets")
  expect(planned.json).toEqual({ text: "gm" })
  expect(planned.headers?.["Authorization"]).toBe("Bearer TOKEN")
})

test("plan Post dm", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "x:dm:12345",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("createDm")
  expect(planned.url.endsWith("/dm_conversations/with/12345/messages")).toBe(true)
  expect(planned.json).toEqual({ text: "hi" })
})

test("plan without token is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "x",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({ tag: "Post", thread_id: "x:777", text: "gm", actions: [] }),
  )
  const result = Effect.runSync(Effect.either(planCommand(post, empty)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left.reason).toContain("bearerToken")
})

test("thread roundtrip overlap capabilities", () => {
  expect(decodeThreadId(encodeThreadId({ kind: "dm", targetId: "5" }))).toEqual({
    kind: "dm",
    targetId: "5",
  })
  expect(decodeThreadId(encodeThreadId({ kind: "tweet", targetId: "5" }))).toEqual({
    kind: "tweet",
    targetId: "5",
  })
  const events = Effect.runSync(
    parseXUpdate({ tweet_create_events: [{ text: "hi", user: { id: "777" } }] }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("x:777")
  expect(x().capabilities()).toEqual(["receive", "send", "reply", "dm"])
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "x",
    config: {},
  })
  expect(verifyX("{}", {}, empty)).toBe(true)
})
