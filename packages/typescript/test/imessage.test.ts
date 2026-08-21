import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  overlapKey,
  parseIMessageUpdate,
  planCommand,
  verifyIMessage,
  imessage,
} from "../src/adapters/imessage.ts"
import {
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "imessage",
  config: { relayUrl: "https://relay.example", apiKey: "sekret" },
})

test("parse relay message", () => {
  const events = Effect.runSync(
    parseIMessageUpdate({
      type: "new-message",
      data: {
        guid: "abc-123",
        text: "hello there",
        handle: { address: "+15551234567" },
        chats: [{ guid: "iMessage;-;+15551234567" }],
        isFromMe: false,
      },
    }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hello there")
  expect(String(event.thread_id)).toBe("imessage:+15551234567")
  expect(event.sender).toBe("+15551234567")
})

test("parse from me returns empty", () => {
  expect(
    Effect.runSync(
      parseIMessageUpdate({
        type: "new-message",
        data: {
          guid: "self-1",
          text: "sent by bot",
          handle: { address: "+15551234567" },
          isFromMe: true,
        },
      }),
    ),
  ).toEqual([])
})

test("parse simplified shape", () => {
  const events = Effect.runSync(
    parseIMessageUpdate({
      from: "alice@example.com",
      text: "hi",
      message_id: "m9",
    }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(String(event.thread_id)).toBe("imessage:alice@example.com")
  expect(event.text).toBe("hi")
})

test("unknown returns empty", () => {
  expect(
    Effect.runSync(
      parseIMessageUpdate({ type: "typing-indicator", data: { guid: "x" } }),
    ),
  ).toEqual([])
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseIMessageUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "imessage:+15551234567",
      text: "yo",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.url).toBe("https://relay.example/api/v1/message/text")
  expect(planned.json).toEqual({ address: "+15551234567", message: "yo" })
  expect(planned.native).toBe("sendText")
  expect(planned.headers?.["Authorization"]).toBe("Bearer sekret")
})

test("plan without apiKey is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "imessage",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "imessage:+15551234567",
      text: "yo",
      actions: [],
    }),
  )
  const result = Effect.runSync(Effect.either(planCommand(post, empty)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left.reason).toContain("apiKey")
})

test("plan Edit unsupported", () => {
  const edit = Effect.runSync(
    decodeCommand({
      tag: "Edit",
      thread_id: "imessage:+15551234567",
      message_id: "m1",
      text: "fixed",
    }),
  )
  const result = Effect.runSync(Effect.either(planCommand(edit, conn)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left.commandTag).toBe("Edit")
})

test("overlap verify capabilities", () => {
  const events = Effect.runSync(
    parseIMessageUpdate({ from: "+15551234567", text: "hi" }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("imessage:+15551234567")
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "imessage",
    config: {},
  })
  expect(verifyIMessage("{}", {}, empty)).toBe(true)
  expect(imessage().capabilities()).toEqual(["receive", "reply", "send", "media"])
})
