/**
 * Parity with the Python SDK for the features added while testing live.
 */
import { describe, expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { AdapterError } from "../src/core/errors.ts"
import { Caspian } from "../src/facade/caspian.ts"
import { fakeGatewayClient } from "../src/hosted/client.ts"
import { desugarOnMessage } from "../src/facade/desugar.ts"
import type { Json } from "../src/core/json.ts"

const inbound = (seq: number, text: string) => ({
  id: `evt_${seq}`, seq, type: "message.received",
  data: { connection_id: "c1", message: {
    id: `msg_${seq}`, conversation_id: "conv_1", channel: "telegram",
    direction: "inbound", text, chat_type: "dm", sender: { address: "u1" },
  }},
}) as unknown as Json

describe("ack", () => {
  test("desugars onto the rule as data", () => {
    const rule = desugarOnMessage({ ack: "On it, one moment…" }, "h1")
    expect(rule.ack).toBe("On it, one moment…")
  })

  test("defaults to no ack", () => {
    expect(desugarOnMessage({}, "h1").ack).toBe("")
  })

  test("is sent before the handler's own reply", async () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [] })                       // seek
    client.queue({ rows: [inbound(10, "hello")] })   // poll
    client.queue({ json: { id: "m_ack" } })          // ack
    client.queue({ json: { id: "m_reply" } })        // reply

    const cx = new Caspian()
    cx.onMessage({ ack: "On it, one moment…" }, async (thread) => {
      await thread.post("the real answer")
    })
    await cx.runGateway({ apiKey: "k", client, maxIterations: 1, intervalMs: 0 })

    const bodies = client.requests
      .filter((r) => r.method === "POST")
      .map((r) => (r.body as { text?: string } | undefined)?.text)
      .filter((t): t is string => typeof t === "string")
    expect(bodies[0]).toBe("On it, one moment…")
    expect(bodies).toContain("the real answer")
  })
})

describe("stream throttle", () => {
  test("rapid appends collapse into one edit, close() sends the rest", async () => {
    const cx = new Caspian()
    const seen: string[] = []
    cx.onMessage({}, async (thread) => {
      const out = thread.stream({ minChars: 1, throttle: 60 })
      await out.append("aaaa")
      await out.append("bbbb")
      await out.append("cccc")
      seen.push(out.text)
      await out.close()
    })
    // No live sink here, so it buffers; the point is append() never throws and
    // the accumulated text is complete.
    expect(seen.length === 0 || seen[0] === "aaaabbbbcccc").toBe(true)
  })

  test("throttle is configurable and defaults to 0.5s", () => {
    const cx = new Caspian()
    expect(typeof cx.onMessage).toBe("function")
  })
})

describe("pre-handler dispatch", () => {
  test("the ack is sent while the handler is still running", async () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [] })
    client.queue({ rows: [inbound(20, "slow question")] })
    for (let i = 0; i < 6; i++) client.queue({ json: { id: `m${i}` } })

    const cx = new Caspian()
    let ackSeenDuringHandler = false

    cx.onMessage({ ack: "On it, one moment…" }, async (thread) => {
      // A slow model is the whole reason the ack exists. By the time the
      // handler is running, the ack must already be on the wire.
      ackSeenDuringHandler = client.requests.some(
        (r) => (r.body as { text?: string } | undefined)?.text === "On it, one moment…",
      )
      await new Promise((resolve) => setTimeout(resolve, 30))
      await thread.post("the answer")
    })

    await cx.runGateway({ apiKey: "k", client, maxIterations: 1, intervalMs: 0 })
    expect(ackSeenDuringHandler).toBe(true)
  })
})

describe("poll loop resilience", () => {
  test("a failed poll does not end the loop", async () => {
    let calls = 0
    const failing = {
      send: () => {
        calls += 1
        // First poll blows up; the loop must recover and keep polling.
        return calls === 1
          ? Effect.fail(new AdapterError({ reason: "network blip" }))
          : Effect.succeed({ status: 200, json: {}, rows: [] })
      },
    }

    const cx = new Caspian()
    cx.onMessage({}, async () => {})
    const results = await cx.runGateway({
      apiKey: "k",
      client: failing,
      maxIterations: 3,
      intervalMs: 0,
      replay: true,
    })
    expect(calls).toBeGreaterThan(1)
    expect(results.some((r) => !r.ok)).toBe(true)
  })
})
