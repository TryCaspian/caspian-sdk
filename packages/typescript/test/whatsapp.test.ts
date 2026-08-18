import { createHmac } from "node:crypto"
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  overlapKey,
  parseWhatsAppUpdate,
  planCommand,
  verifyWhatsApp,
  whatsapp,
} from "../src/adapters/whatsapp.ts"
import {
  AdapterError,
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("wa1"),
  channel: "whatsapp",
  config: { accessToken: "TKN", phoneNumberId: "111222" },
})

const messagesWebhook = (message: Record<string, unknown>) => ({
  object: "whatsapp_business_account",
  entry: [
    {
      id: "WABA",
      changes: [{ field: "messages", value: { messages: [message] } }],
    },
  ],
})

test("parse text message", () => {
  const events = Effect.runSync(
    parseWhatsAppUpdate(
      messagesWebhook({
        from: "15551234567",
        id: "wamid.ABC",
        type: "text",
        text: { body: "hello" },
      }),
    ),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hello")
  expect(String(event.thread_id)).toBe("whatsapp:15551234567")
  expect(event.chat_kind).toBe("dm")
  expect(event.sender).toBe("15551234567")
})

test("parse reaction message", () => {
  const events = Effect.runSync(
    parseWhatsAppUpdate(
      messagesWebhook({
        from: "15551234567",
        id: "wamid.R",
        type: "reaction",
        reaction: { message_id: "wamid.ABC", emoji: "👍" },
      }),
    ),
  )
  const event = events[0]
  expect(event?.kind).toBe("reaction")
  if (event?.kind !== "reaction") {
    return
  }
  expect(event.emoji).toBe("👍")
})

test("status receipts become Receipt events", () => {
  const events = Effect.runSync(
    parseWhatsAppUpdate({
      entry: [
        {
          changes: [
            {
              value: {
                statuses: [
                  { id: "wamid.OUT", status: "read", recipient_id: "15551234567" },
                ],
              },
            },
          ],
        },
      ],
    }),
  )
  expect(events).toHaveLength(1)
  expect(events[0]?.kind).toBe("receipt")
  if (events[0]?.kind !== "receipt") {
    return
  }
  expect(events[0].status).toBe("read")
  expect(events[0].message_id).toBe("wamid.OUT")
  expect(String(events[0].thread_id)).toBe("whatsapp:15551234567")
})

test("unknown returns empty", () => {
  expect(Effect.runSync(parseWhatsAppUpdate({ foo: "bar" }))).toEqual([])
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseWhatsAppUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post text", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "whatsapp:15551234567",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.url).toBe("https://graph.facebook.com/v21.0/111222/messages")
  expect(planned.native).toBe("text")
  expect(planned.headers?.["Authorization"]).toBe("Bearer TKN")
  expect(planned.json).toEqual({
    messaging_product: "whatsapp",
    to: "15551234567",
    type: "text",
    text: { body: "hi" },
  })
})

test("plan Post with buttons", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "whatsapp:15551234567",
      text: "pick",
      actions: [
        { label: "Yes", data: "y" },
        { label: "No", data: "n" },
      ],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  if (planned?.transport !== "http_json") {
    return
  }
  const body = planned.json as {
    type: string
    interactive: { action: { buttons: ReadonlyArray<{ reply: unknown }> } }
  }
  expect(body.type).toBe("interactive")
  expect(body.interactive.action.buttons).toHaveLength(2)
})

test("plan React", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "whatsapp:15551234567",
      message_id: "wamid.ABC",
      emoji: "❤️",
    }),
  )
  const planned = Effect.runSync(planCommand(react, conn))
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("reaction")
  expect(planned.json).toMatchObject({
    type: "reaction",
    reaction: { message_id: "wamid.ABC", emoji: "❤️" },
  })
})

test("plan without token is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c"),
    channel: "whatsapp",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "whatsapp:15551234567",
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
  expect(result.left.reason).toContain("accessToken")
})

test("verify signature", () => {
  const signed = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c"),
    channel: "whatsapp",
    config: { appSecret: "shh" },
  })
  const body = '{"entry":[]}'
  const digest = createHmac("sha256", "shh").update(body).digest("hex")
  expect(
    verifyWhatsApp(body, { "X-Hub-Signature-256": `sha256=${digest}` }, signed),
  ).toBe(true)
  expect(
    verifyWhatsApp(body, { "X-Hub-Signature-256": "sha256=nope" }, signed),
  ).toBe(false)
  expect(whatsapp().capabilities()).toContain("react")
  const events = Effect.runSync(
    parseWhatsAppUpdate(
      messagesWebhook({
        from: "15551234567",
        type: "text",
        text: { body: "hi" },
      }),
    ),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("whatsapp:15551234567")
})
