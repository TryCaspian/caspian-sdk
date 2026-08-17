import { createHmac } from "node:crypto"
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  overlapKey,
  parseMessengerUpdate,
  planCommand,
  verifyMessenger,
  messenger,
} from "../src/adapters/messenger.ts"
import {
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("m1"),
  channel: "messenger",
  config: { pageAccessToken: "PTKN" },
})

const messagingWebhook = (messaging: Record<string, unknown>) => ({
  object: "page",
  entry: [{ id: "PAGE", messaging: [messaging] }],
})

test("parse text message", () => {
  const events = Effect.runSync(
    parseMessengerUpdate(
      messagingWebhook({
        sender: { id: "PSID1" },
        recipient: { id: "PAGE" },
        message: { mid: "mid.1", text: "hello" },
      }),
    ),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hello")
  expect(String(event.thread_id)).toBe("messenger:PSID1")
  expect(event.chat_kind).toBe("dm")
})

test("parse postback as action", () => {
  const events = Effect.runSync(
    parseMessengerUpdate(
      messagingWebhook({
        sender: { id: "PSID1" },
        recipient: { id: "PAGE" },
        postback: { title: "Get Started", payload: "START" },
      }),
    ),
  )
  const event = events[0]
  expect(event?.kind).toBe("action")
  if (event?.kind !== "action") {
    return
  }
  expect(event.data).toBe("START")
})

test("unknown messaging returns empty", () => {
  expect(
    Effect.runSync(
      parseMessengerUpdate(
        messagingWebhook({ sender: { id: "PSID1" }, delivery: { watermark: 1 } }),
      ),
    ),
  ).toEqual([])
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseMessengerUpdate("not json")))
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
      thread_id: "messenger:PSID1",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.url).toBe("https://graph.facebook.com/v20.0/me/messages")
  expect(planned.json).toEqual({
    recipient: { id: "PSID1" },
    message: { text: "hi" },
  })
})

test("plan Post with quick replies", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "messenger:PSID1",
      text: "pick",
      actions: [{ label: "Yes", data: "y" }],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.json).toMatchObject({
    message: {
      text: "pick",
      quick_replies: [
        { content_type: "text", title: "Yes", payload: "y" },
      ],
    },
  })
})

test("plan Typing", () => {
  const typing = Effect.runSync(
    decodeCommand({ tag: "Typing", thread_id: "messenger:PSID1" }),
  )
  const planned = Effect.runSync(planCommand(typing, conn))
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("typing_on")
  expect(planned.json).toEqual({
    recipient: { id: "PSID1" },
    sender_action: "typing_on",
  })
})

test("plan React unsupported", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "messenger:PSID1",
      message_id: "mid.1",
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

test("verify and overlap", () => {
  const signed = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c"),
    channel: "messenger",
    config: { appSecret: "shh" },
  })
  const body = '{"entry":[]}'
  const digest = createHmac("sha256", "shh").update(body).digest("hex")
  expect(
    verifyMessenger(body, { "X-Hub-Signature-256": `sha256=${digest}` }, signed),
  ).toBe(true)
  expect(messenger().capabilities()).toContain("typing")
  const events = Effect.runSync(
    parseMessengerUpdate(
      messagingWebhook({
        sender: { id: "PSID1" },
        message: { text: "hi" },
      }),
    ),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("messenger:PSID1")
})
