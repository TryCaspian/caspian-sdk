import { createHmac } from "node:crypto"
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  overlapKey,
  parseLinearUpdate,
  planCommand,
  verifyLinear,
  linear,
} from "../src/adapters/linear.ts"
import {
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "linear",
  config: { apiKey: "lin_key" },
})

test("parse comment webhook", () => {
  const events = Effect.runSync(
    parseLinearUpdate({
      type: "Comment",
      action: "create",
      data: {
        id: "comment-1",
        body: "looks good",
        issue: { id: "issue-9" },
        user: { id: "user-3" },
      },
    }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("looks good")
  expect(event.sender).toBe("user-3")
  expect(event.chat_kind).toBe("channel")
  expect(String(event.thread_id)).toBe("linear:issue-9")
})

test("parse issue webhook", () => {
  const events = Effect.runSync(
    parseLinearUpdate({
      type: "Issue",
      action: "create",
      data: { id: "issue-9", title: "Fix the bug" },
    }),
  )
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("Fix the bug")
  expect(String(event.thread_id)).toBe("linear:issue-9")
})

test("unknown type returns empty", () => {
  expect(
    Effect.runSync(
      parseLinearUpdate({ type: "Reaction", action: "create", data: { id: "r1" } }),
    ),
  ).toEqual([])
})

test("invalid JSON is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseLinearUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post commentCreate", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "linear:issue-9",
      text: "on it",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.native).toBe("commentCreate")
  expect(planned.url).toBe("https://api.linear.app/graphql")
  expect(planned.headers?.["Authorization"]).toBe("lin_key")
  expect(planned.json).toMatchObject({
    variables: { input: { issueId: "issue-9", body: "on it" } },
  })
  expect(String(planned.json?.["query"])).toContain("commentCreate")
})

test("plan without apiKey is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "linear",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "linear:issue-9",
      text: "on it",
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

test("verify signature overlap capabilities", () => {
  const body = '{"type":"Comment"}'
  const secret = "whsec"
  const signed = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "linear",
    config: { webhookSecret: secret },
  })
  const sig = createHmac("sha256", secret).update(body).digest("hex")
  expect(verifyLinear(body, { "Linear-Signature": sig }, signed)).toBe(true)
  expect(verifyLinear(body, { "Linear-Signature": "nope" }, signed)).toBe(false)
  const events = Effect.runSync(
    parseLinearUpdate({
      type: "Issue",
      data: { id: "issue-9", title: "hi" },
    }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("linear:issue-9")
  expect(linear().capabilities()).toEqual(["receive", "reply", "send", "threading"])
})
