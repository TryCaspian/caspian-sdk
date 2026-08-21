import { expect, test } from "bun:test"
import { fakeGatewayClient } from "caspian"
import * as Effect from "effect/Effect"
import { parseArgv } from "../src/desugar.ts"
import { runIntent } from "../src/run.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

test("threads ls lists conversations", () => {
  const gw = fakeGatewayClient()
  gw.queue({
    rows: [{ id: "telegram:123:456", channel: "telegram" }],
  })
  const out = sync(
    runIntent(sync(parseArgv(["threads", "ls", "--channel", "telegram"])), gw),
  ) as ReadonlyArray<{ readonly id: string }>
  expect(gw.requests).toEqual([{ method: "GET", path: "/v1/conversations" }])
  expect(out[0]?.id).toBe("telegram:123:456")
})

test("threads tail gets events", () => {
  const gw = fakeGatewayClient()
  gw.queue({ rows: [{ seq: 1, type: "message.received" }] })
  const out = sync(
    runIntent(sync(parseArgv(["threads", "tail", "telegram:123:456"])), gw),
  ) as ReadonlyArray<{ readonly seq: number }>
  expect(gw.requests).toEqual([
    {
      method: "GET",
      path: "/v1/events",
      params: { after_seq: "0", limit: "100" },
    },
  ])
  expect(out[0]?.seq).toBe(1)
})
