/**
 * The facade against the real gateway contract.
 *
 * Before this, cx.run() wired to an interpreter that posted every command to
 * /v1/outbox (an endpoint the gateway does not expose) and had no inbound path
 * at all, so hosted mode could not work in either direction.
 */
import { describe, expect, test } from "bun:test"
import { Caspian } from "../src/facade/caspian.ts"
import { fakeGatewayClient } from "../src/hosted/client.ts"
import type { Json } from "../src/core/json.ts"

const inbound = (seq: number, text: string) => ({
  id: `evt_${seq}`,
  seq,
  type: "message.received",
  data: {
    connection_id: "conn_1",
    message: {
      id: `msg_${seq}`,
      conversation_id: "conv_1",
      channel: "telegram",
      direction: "inbound",
      text,
      chat_type: "dm",
      sender: { address: "u1" },
    },
  },
}) as unknown as Json

describe("runGateway", () => {
  test("an inbound gateway event reaches the handler and the reply goes back", async () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [] })                    // seek: no history
    client.queue({ rows: [inbound(10, "hello")] }) // first real poll
    client.queue({ json: { id: "msg_out" } })      // the reply

    const cx = new Caspian()
    const seen: string[] = []
    cx.onMessage({}, async (thread, msg) => {
      seen.push(msg.kind === "message" ? msg.text : "")
      await thread.post("hi back")
    })

    await cx.run({ apiKey: "k", client, maxIterations: 1, intervalMs: 0 })

    expect(seen).toEqual(["hello"])
    const paths = client.requests.map((r) => r.path)
    expect(paths).toContain("/v1/events")
    expect(paths.some((p) => p.includes("/reply") || p.includes("/messages"))).toBe(true)
    expect(paths.every((p) => !p.includes("outbox"))).toBe(true)
  })

  test("history is not replayed on a fresh start", async () => {
    const client = fakeGatewayClient()
    client.queue({ rows: [inbound(5, "old message")] }) // seek swallows it
    client.queue({ rows: [] })

    const cx = new Caspian()
    const seen: string[] = []
    cx.onMessage({}, async (_thread, msg) => {
      seen.push(msg.kind === "message" ? msg.text : "")
    })

    await cx.run({ apiKey: "k", client, maxIterations: 1, intervalMs: 0 })
    expect(seen).toEqual([])
  })
})
