/**
 * Contract tests against the REAL gateway shapes.
 *
 * The first TypeScript hosted layer posted every command to /v1/outbox, an
 * endpoint that does not exist, and had no inbound polling at all. These
 * fixtures are verbatim copies of what api.trycaspianai.com actually returns,
 * so drift fails here instead of silently doing nothing in production.
 */
import { describe, expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import type { Json } from "../src/core/json.ts"
import { fakeGatewayClient } from "../src/hosted/client.ts"
import { gatewayPoller, parseBatch } from "../src/hosted/inbound.ts"
import { toRequest } from "../src/hosted/outbound.ts"

const REAL_EVENT = {
  id: "evt_1",
  seq: 24210,
  type: "message.received",
  occurred_at: "2026-08-17T13:18:00Z",
  data: {
    customer_id: "cus_1",
    agent_id: "agt_1",
    connection_id: "conn_1",
    message: {
      id: "msg_1",
      conversation_id: "conv_1",
      connection_id: "conn_1",
      channel: "discord",
      direction: "inbound",
      status: "received",
      sender: { address: "madmecodes", name: "Ayush gupta" },
      text: "what can u do?",
      chat_type: "channel",
      media: [],
    },
  },
} as unknown as Json

const run = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

describe("event shape", () => {
  test("the real EventOut envelope parses", () => {
    const events = parseBatch({ events: [REAL_EVENT] } as unknown as Json)
    expect(events.length).toBe(1)
    expect(events[0]!.kind).toBe("message")
    expect(String(events[0]!.thread_id)).toBe("discord:conv_1")
    expect((events[0] as { text: string }).text).toBe("what can u do?")
  })

  test("our own outbound echo is not work", () => {
    const echo = JSON.parse(JSON.stringify(REAL_EVENT))
    echo.type = "message.sent"
    echo.data.message.direction = "outbound"
    expect(parseBatch({ events: [echo] } as unknown as Json).length).toBe(0)
  })
})

describe("poller contract", () => {
  test("pages by after_seq and limit, never a cursor token", () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [REAL_EVENT] })
    run(gatewayPoller(client, { replay: true }).fetchRaw())
    const params = client.requests[0]!.params ?? {}
    expect(params["after_seq"]).toBeDefined()
    expect(params["limit"]).toBeDefined()
    expect(params["cursor"]).toBeUndefined()
  })

  test("an array body is not dropped", () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [REAL_EVENT] })
    const body = run(gatewayPoller(client, { replay: true }).fetchRaw())
    expect((body as { events: unknown[] }).events.length).toBe(1)
  })

  test("a fresh poller skips history instead of re-answering it", () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [REAL_EVENT] }) // consumed by the seek
    client.queue({ rows: [] })
    const poller = gatewayPoller(client)
    const body = run(poller.fetchRaw())
    expect((body as { events: unknown[] }).events.length).toBe(0)
    expect(poller.cursor()).toBe(24210)
  })
})

describe("outbound paths", () => {
  const post = {
    tag: "Post",
    thread_id: "discord:conv_1",
    text: "hi",
    actions: [],
    standalone: false,
  } as unknown as Parameters<typeof toRequest>[0]

  test("post threads onto the message that triggered the turn", () => {
    const request = run(toRequest(post, { replyTo: "msg_1" }))
    expect(request.path).toBe("/v1/messages/msg_1/reply")
  })

  test("post with no trigger is a plain send", () => {
    const request = run(toRequest(post))
    expect(request.path).toBe("/v1/conversations/conv_1/messages")
  })

  test("a standalone post never threads", () => {
    const standalone = { ...(post as object), standalone: true } as unknown as typeof post
    const request = run(toRequest(standalone, { replyTo: "msg_1" }))
    expect(request.path).toBe("/v1/conversations/conv_1/messages")
  })

  test("typing targets the message, not the conversation", () => {
    const typing = {
      tag: "Typing",
      thread_id: "discord:conv_1",
    } as unknown as Parameters<typeof toRequest>[0]
    const request = run(toRequest(typing, { replyTo: "msg_1" }))
    expect(request.path).toBe("/v1/messages/msg_1/typing")
    expect(request.path).not.toContain("conversations")
  })

  test("commands the gateway cannot do fail loudly", () => {
    for (const tag of ["Delete", "Pin", "Unpin", "Forward", "MarkRead"]) {
      const command = {
        tag,
        thread_id: "discord:conv_1",
        message_id: "m1",
      } as unknown as Parameters<typeof toRequest>[0]
      const exit = Effect.runSyncExit(toRequest(command))
      expect(exit._tag).toBe("Failure")
    }
  })

  test("no request targets the fictional /v1/outbox", () => {
    const request = run(toRequest(post, { replyTo: "msg_1" }))
    expect(request.path).not.toContain("outbox")
  })
})
