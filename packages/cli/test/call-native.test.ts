import { expect, test } from "bun:test"
import { fakeGatewayClient, toRequest } from "caspian-sdk"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import type { Command } from "caspian-sdk"
import { parseArgv } from "../src/desugar.ts"
import { runIntent } from "../src/run.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

const failReason = <A, E extends { readonly reason: string }>(
  effect: Effect.Effect<A, E>,
): string => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isRight(result)) {
    throw new Error("expected runIntent to fail")
  }
  return result.left.reason
}

test("call telegram.send-photo follows hosted outbound SendMedia", () => {
  const gw = fakeGatewayClient()
  const mapped = Effect.runSync(
    Effect.either(
      toRequest({
        tag: "SendMedia",
        thread_id: "telegram:123:456",
        attachment: {
          type: "photo",
          url: "./graph.png",
          file_id: "",
          filename: "graph.png",
          mime_type: "",
          size_bytes: 0,
          caption: "",
        },
        caption: "",
      } as Command),
    ),
  )

  const program = runIntent(
    sync(
      parseArgv([
        "call",
        "telegram.send-photo",
        "--thread",
        "telegram:123:456",
        "--file",
        "./graph.png",
      ]),
    ),
    gw,
  )

  if (Either.isLeft(mapped)) {
    expect(failReason(program)).toContain("SendMedia")
    expect(gw.requests).toEqual([])
    return
  }

  gw.queue({ json: { ok: true } })
  sync(program)
  expect(gw.requests[0]?.method).toBe(mapped.right.method)
  expect(gw.requests[0]?.path).toBe(mapped.right.path)
  const body = gw.requests[0]?.body ?? {}
  expect("chat_id" in body).toBe(false)
})
