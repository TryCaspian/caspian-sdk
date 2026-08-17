import { createHmac } from "node:crypto"
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  encodeThreadId,
  overlapKey,
  parseSlackUpdate,
  planCommand,
  slack,
  verifySlack,
} from "../src/adapters/slack.ts"
import {
  AdapterError,
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("conn1"),
  channel: "slack",
  config: { botToken: "xoxb-123" },
})

const eventCallback = (inner: Record<string, unknown>) => ({
  type: "event_callback",
  event: inner,
})

test("parse text message", () => {
  const events = Effect.runSync(
    parseSlackUpdate(
      eventCallback({
        type: "message",
        user: "U1",
        channel: "C1",
        ts: "1360782400.498405",
        text: "hello",
      }),
    ),
  )
  expect(events).toHaveLength(1)
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hello")
  expect(String(event.thread_id)).toBe("slack:C1")
  expect(event.chat_kind).toBe("channel")
  expect(event.sender).toBe("U1")
})

test("parse threaded message", () => {
  const events = Effect.runSync(
    parseSlackUpdate(
      eventCallback({
        type: "message",
        user: "U1",
        channel: "C1",
        ts: "2.0",
        thread_ts: "1.0",
        text: "re",
      }),
    ),
  )
  expect(String(events[0]?.thread_id)).toBe("slack:C1:1.0")
})

test("parse skips bot message", () => {
  const events = Effect.runSync(
    parseSlackUpdate(
      eventCallback({
        type: "message",
        bot_id: "B1",
        channel: "C1",
        text: "x",
      }),
    ),
  )
  expect(events).toEqual([])
})

test("parse block_actions to action", () => {
  const events = Effect.runSync(
    parseSlackUpdate({
      type: "block_actions",
      user: { id: "U9" },
      trigger_id: "trig1",
      channel: { id: "C1" },
      message: { ts: "5.0" },
      actions: [{ action_id: "approve", value: "v1" }],
    }),
  )
  expect(events[0]?.kind).toBe("action")
  if (events[0]?.kind !== "action") {
    return
  }
  expect(events[0].data).toBe("approve")
  expect(String(events[0].thread_id)).toBe("slack:C1")
  expect(events[0].raw["trigger_id"]).toBe("trig1")
})

test("parse block_actions form-encoded", () => {
  const payload = {
    type: "block_actions",
    user: { id: "U9" },
    channel: { id: "C1" },
    message: { ts: "5.0" },
    actions: [{ action_id: "click", value: "v" }],
  }
  const body = new URLSearchParams({ payload: JSON.stringify(payload) }).toString()
  const events = Effect.runSync(parseSlackUpdate(body))
  expect(events[0]?.kind).toBe("action")
  if (events[0]?.kind !== "action") {
    return
  }
  expect(events[0].data).toBe("click")
})

test("parse reaction_added", () => {
  const events = Effect.runSync(
    parseSlackUpdate(
      eventCallback({
        type: "reaction_added",
        user: "U1",
        reaction: "thumbsup",
        item: { type: "message", channel: "C1", ts: "5.0" },
      }),
    ),
  )
  expect(events[0]?.kind).toBe("reaction")
  if (events[0]?.kind !== "reaction") {
    return
  }
  expect(events[0].emoji).toBe("thumbsup")
  expect(events[0].sender).toBe("U1")
})

test("url_verification returns empty", () => {
  expect(
    Effect.runSync(
      parseSlackUpdate({ type: "url_verification", challenge: "abc123" }),
    ),
  ).toEqual([])
})

test("unknown type returns empty", () => {
  expect(Effect.runSync(parseSlackUpdate({ type: "team_join" }))).toEqual([])
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseSlackUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post chat.postMessage", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "slack:C1",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("chat.postMessage")
  expect(planned.url).toBe("https://slack.com/api/chat.postMessage")
  expect(planned.json).toEqual({ channel: "C1", text: "hi" })
  expect(planned.headers?.["Authorization"]).toBe("Bearer xoxb-123")
})

test("plan React reactions.add", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "slack:C1",
      message_id: "5.0",
      emoji: "thumbsup",
    }),
  )
  const planned = Effect.runSync(planCommand(react, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("reactions.add")
  expect(planned.json).toEqual({
    channel: "C1",
    timestamp: "5.0",
    name: "thumbsup",
  })
})

test("plan without token is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "slack",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "slack:C1",
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
  expect(result.left.reason).toContain("botToken")
})

test("Typing is AdapterError", () => {
  const typing = Effect.runSync(
    decodeCommand({ tag: "Typing", thread_id: "slack:C1" }),
  )
  const result = Effect.runSync(Effect.either(planCommand(typing, conn)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left.commandTag).toBe("Typing")
})

test("verify true when no secret", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "slack",
    config: {},
  })
  expect(verifySlack("{}", {}, empty)).toBe(true)
})

test("verify checks signature", () => {
  const secret = "s3cr3t"
  const signed = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "slack",
    config: { signingSecret: secret },
  })
  const body = '{"type":"event_callback"}'
  const ts = "1531420618"
  const digest = createHmac("sha256", secret)
    .update(`v0:${ts}:${body}`)
    .digest("hex")
  expect(
    verifySlack(body, {
      "X-Slack-Request-Timestamp": ts,
      "X-Slack-Signature": `v0=${digest}`,
    }, signed),
  ).toBe(true)
  expect(
    verifySlack(body, {
      "X-Slack-Request-Timestamp": ts,
      "X-Slack-Signature": "v0=deadbeef",
    }, signed),
  ).toBe(false)
})

test("overlap key is slack thread id", () => {
  const encoded = encodeThreadId({ channel: "C1", threadTs: "1.0" })
  expect(String(encoded)).toBe("slack:C1:1.0")
  const events = Effect.runSync(
    parseSlackUpdate(
      eventCallback({
        type: "message",
        user: "U1",
        channel: "C1",
        ts: "2.0",
        thread_ts: "1.0",
        text: "re",
      }),
    ),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("slack:C1:1.0")
})

test("format escapes mrkdwn", () => {
  expect(slack().format("a & b < c")).toBe("a &amp; b &lt; c")
})
