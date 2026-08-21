/**
 * Skill gates for packages/cli.
 *
 * sdk-reliability: failure is data; effects behind GatewayClient; chaos
 * interpreter; bounded tail; no throw across the CLI algebra.
 * functional-dsl: argv → Intent (syntax) → Plan (denotation) → interpreter;
 * illegal Call bags gone; the same Plan runs as dry-run or eval.
 */
import { expect, test } from "bun:test"
import { fakeGatewayClient } from "caspian"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { getCatalog } from "../src/catalog.ts"
import { chaosGatewayClient } from "../src/chaos.ts"
import { parseArgv } from "../src/desugar.ts"
import { planIntent } from "../src/plan.ts"
import { runIntent, runPlan } from "../src/run.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

const left = <A, E extends { readonly reason: string }>(
  effect: Effect.Effect<A, E>,
): E => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isRight(result)) {
    throw new Error("expected failure as data")
  }
  return result.left
}

test("unknown catalog id is UsageError data, not a throw", () => {
  const result = Effect.runSync(Effect.either(getCatalog("nope")))
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isLeft(result)) {
    expect(result.left._tag).toBe("UsageError")
    expect(result.left.reason).toContain("caspian catalog search")
  }
})

test("missing flag value is UsageError data, not a throw", () => {
  expect(left(parseArgv(["call", "post", "--thread"])).reason).toContain(
    "--thread",
  )
})

test("planIntent is the denotation: call post is a Gateway request, no HTTP", () => {
  const intent = sync(
    parseArgv([
      "call",
      "post",
      "--thread",
      "telegram:123:456",
      "--text",
      "shipping now",
    ]),
  )
  const plan = sync(planIntent(intent))
  expect(plan._tag).toBe("Gateway")
  if (plan._tag === "Gateway") {
    expect(plan.request).toEqual({
      method: "POST",
      path: "/v1/conversations/123:456/messages",
      body: { text: "shipping now" },
    })
  }
})

test("the same Plan evals against a recording client", () => {
  const intent = sync(
    parseArgv([
      "call",
      "post",
      "--thread",
      "slack:C123:ts",
      "--text",
      "shipped",
    ]),
  )
  const plan = sync(planIntent(intent))
  const gw = fakeGatewayClient()
  gw.queue({ json: { ok: true, id: "msg_1" } })
  sync(runPlan(plan, gw))
  expect(gw.requests[0]?.path).toBe("/v1/conversations/C123:ts/messages")
})

test("chaos gateway is a UsageError, never a thrown exception", () => {
  const intent = sync(
    parseArgv([
      "call",
      "post",
      "--thread",
      "telegram:1",
      "--text",
      "hi",
    ]),
  )
  expect(() =>
    Effect.runSync(Effect.either(runIntent(intent, chaosGatewayClient()))),
  ).not.toThrow()
  const error = left(runIntent(intent, chaosGatewayClient()))
  expect(error._tag).toBe("UsageError")
  expect(error.reason).toContain("chaos")
})

test("self-host without a bot token is unplannable", () => {
  const intent = sync(
    parseArgv(["channels", "add", "telegram", "--via", "self-host"]),
  )
  expect(left(planIntent(intent)).reason).toContain("--bot-token")
})

test("login is a Plan, not a hosted Gateway request", () => {
  const intent = sync(parseArgv(["login", "--open", "--gateway", "https://gw.example"]))
  const plan = sync(planIntent(intent))
  expect(plan._tag).toBe("Login")
  if (plan._tag === "Login") {
    expect(plan.gateway).toBe("https://gw.example")
    expect(plan.open).toBe(true)
  }
})

test("init is a Plan, not sandbox mint", () => {
  const intent = sync(parseArgv(["init", "project"]))
  const plan = sync(planIntent(intent))
  expect(plan._tag).toBe("Init")
  if (plan._tag === "Init") {
    expect(plan.kind).toBe("project")
  }
})

test("threads tail is bounded", () => {
  const plan = sync(planIntent(sync(parseArgv(["threads", "tail"]))))
  expect(plan._tag).toBe("Gateway")
  if (plan._tag === "Gateway") {
    expect(plan.request.params?.["limit"]).toBeDefined()
    expect(Number(plan.request.params?.["limit"])).toBeLessThanOrEqual(500)
  }
})
