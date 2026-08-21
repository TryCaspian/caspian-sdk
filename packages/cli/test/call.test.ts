import { expect, test } from "bun:test"
import { fakeGatewayClient } from "caspian"
import * as Effect from "effect/Effect"
import { parseArgv } from "../src/desugar.ts"
import { runIntent } from "../src/run.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

test("call post uses conversation messages", () => {
  const gw = fakeGatewayClient()
  gw.queue({ json: { ok: true, id: "msg_1" } })
  sync(
    runIntent(
      sync(
        parseArgv([
          "call",
          "post",
          "--thread",
          "telegram:123:456",
          "--text",
          "shipping now",
        ]),
      ),
      gw,
    ),
  )
  expect(gw.requests).toEqual([
    {
      method: "POST",
      path: "/v1/conversations/123:456/messages",
      body: { text: "shipping now" },
    },
  ])
})

test("call post on slack is the same command", () => {
  const gw = fakeGatewayClient()
  gw.queue({ json: { ok: true, id: "msg_1" } })
  sync(
    runIntent(
      sync(
        parseArgv([
          "call",
          "post",
          "--thread",
          "slack:C123:ts",
          "--text",
          "shipped",
        ]),
      ),
      gw,
    ),
  )
  expect(gw.requests[0]?.path).toBe("/v1/conversations/C123:ts/messages")
  const body = gw.requests[0]?.body ?? {}
  expect("chat_id" in body).toBe(false)
  expect("thread_id" in body).toBe(false)
})
