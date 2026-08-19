import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { parseArgv } from "../src/desugar.ts"
import type { Call, ChannelsAdd } from "../src/intent.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

const failReason = <A, E extends { readonly reason: string }>(
  effect: Effect.Effect<A, E>,
): string => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isRight(result)) {
    throw new Error("expected parseArgv to fail")
  }
  return result.left.reason
}

test("channels add telegram omitting via is hosted", () => {
  const intent = sync(parseArgv(["channels", "add", "telegram"])) as ChannelsAdd
  expect(intent._tag).toBe("ChannelsAdd")
  expect(intent.channel).toBe("telegram")
  expect(intent.via).toBe("hosted")
})

test("channels add self-host", () => {
  const intent = sync(
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
  ) as ChannelsAdd
  expect(intent).toEqual({
    _tag: "ChannelsAdd",
    channel: "telegram",
    via: "self-host",
    display_name: "",
    bot_token: "123:abc",
    webhook_url: "https://example.com/hook",
    inbound: true,
  })
})

test("call post is the send path", () => {
  const intent = sync(
    parseArgv([
      "call",
      "post",
      "--thread",
      "telegram:123:456",
      "--text",
      "shipping now",
    ]),
  ) as Call
  expect(intent).toEqual({
    _tag: "Call",
    id: "post",
    args: { thread_id: "telegram:123:456", text: "shipping now" },
  })
})

test("call native id is still call", () => {
  const intent = sync(
    parseArgv([
      "call",
      "telegram.send-photo",
      "--thread",
      "telegram:123:456",
      "--file",
      "./graph.png",
    ]),
  ) as Call
  expect(intent).toEqual({
    _tag: "Call",
    id: "telegram.send-photo",
    args: { thread_id: "telegram:123:456", file: "./graph.png" },
  })
})

test("connect is error", () => {
  expect(failReason(parseArgv(["connect", "telegram"]))).toContain(
    "caspian channels add",
  )
})

test("channel verb is error use call", () => {
  expect(
    failReason(
      parseArgv([
        "telegram",
        "send-photo",
        "--thread",
        "telegram:1",
        "--file",
        "x.png",
      ]),
    ),
  ).toContain("caspian call")
})

test("threads reply is error use call post", () => {
  expect(
    failReason(
      parseArgv(["threads", "reply", "telegram:123:456", "--text", "on my way"]),
    ),
  ).toContain("caspian call post")
})

test("channels watch is error use threads tail", () => {
  expect(failReason(parseArgv(["channels", "watch"]))).toContain(
    "caspian threads tail",
  )
})
