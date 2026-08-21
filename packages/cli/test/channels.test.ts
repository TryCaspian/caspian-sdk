import { expect, test } from "bun:test"
import { fakeGatewayClient } from "caspian-sdk"
import * as Effect from "effect/Effect"
import { parseArgv } from "../src/desugar.ts"
import { runIntent } from "../src/run.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

test("hosted channels add posts connection", () => {
  const gw = fakeGatewayClient()
  gw.queue({
    json: { id: "conn_1", channel: "telegram", status: "active" },
  })
  const out = sync(
    runIntent(sync(parseArgv(["channels", "add", "telegram"])), gw),
  ) as { readonly id: string }
  expect(gw.requests).toEqual([
    { method: "POST", path: "/v1/connections/telegram", body: { wait: true } },
  ])
  expect(out.id).toBe("conn_1")
})

test("self-host does not call gateway", () => {
  const gw = fakeGatewayClient()
  const out = sync(
    runIntent(
      sync(
        parseArgv([
          "channels",
          "add",
          "telegram",
          "--via",
          "self-host",
          "--bot-token",
          "123:abc",
          "--webhook-url",
          "https://example.com/hook",
        ]),
      ),
      gw,
    ),
  ) as { readonly via: string; readonly channel: string }
  expect(gw.requests).toEqual([])
  expect(out.via).toBe("self-host")
  expect(out.channel).toBe("telegram")
})

test("hosted slack add uses the install path", () => {
  const gw = fakeGatewayClient()
  gw.queue({ json: { authorize_url: "https://example.com/oauth" } })
  sync(runIntent(sync(parseArgv(["channels", "add", "slack"])), gw))
  expect(gw.requests[0]?.path).toBe("/v1/connections/slack/install")
})

test("channels ls gets connections", () => {
  const gw = fakeGatewayClient()
  gw.queue({ rows: [{ id: "conn_1", channel: "telegram" }] })
  const out = sync(runIntent(sync(parseArgv(["channels", "ls"])), gw)) as ReadonlyArray<{
    readonly id: string
  }>
  expect(gw.requests).toEqual([{ method: "GET", path: "/v1/connections" }])
  expect(out[0]?.id).toBe("conn_1")
})
