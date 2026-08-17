import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { Caspian } from "../src/facade/caspian.ts"
import { addChannel, decodeChannelAdd } from "../src/provision/add.ts"

const runEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

test("via is required — omitting it is DecodeError, not hosted", () => {
  const result = runEither(decodeChannelAdd({}))
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("DecodeError")
})

test("via must be hosted or self-host — not oauth, not credentials", () => {
  const oauth = runEither(decodeChannelAdd({ via: "oauth" }))
  const creds = runEither(decodeChannelAdd({ via: "credentials" }))
  expect(Either.isLeft(oauth)).toBe(true)
  expect(Either.isLeft(creds)).toBe(true)
})

test("self-host without botToken is DecodeError", () => {
  const result = runEither(
    decodeChannelAdd({
      via: "self-host",
      webhookUrl: "https://myapp.example.com/api/webhooks/telegram",
    }),
  )
  expect(Either.isLeft(result)).toBe(true)
})

test("self-host with inbound and no webhookUrl is ProvisionError", () => {
  const result = runEither(
    addChannel("telegram", {
      via: "self-host",
      botToken: "tok",
    }),
  )
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("ProvisionError")
})

test("self-host send-only (inbound: false) does not need webhookUrl", () => {
  const result = runEither(
    addChannel("telegram", {
      via: "self-host",
      botToken: "tok",
      inbound: false,
    }),
  )
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.via).toBe("self-host")
  expect(result.right.channel).toBe("telegram")
  expect(result.right.config.botToken).toBe("tok")
  expect(result.right.config.inbound).toBe(false)
})

test("self-host with token and webhookUrl mints a Connection", () => {
  const result = runEither(
    addChannel("telegram", {
      via: "self-host",
      botToken: "tok",
      webhookUrl: "https://myapp.example.com/api/webhooks/telegram",
    }),
  )
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.via).toBe("self-host")
  expect(result.right.config.webhookUrl).toBe(
    "https://myapp.example.com/api/webhooks/telegram",
  )
})

test("hosted rejects botToken (Caspian owns the secret)", () => {
  const result = runEither(
    decodeChannelAdd({ via: "hosted", botToken: "nope" }),
  )
  expect(Either.isLeft(result)).toBe(true)
})

test("hosted mints a Connection with via hosted", () => {
  const result = runEither(addChannel("telegram", { via: "hosted" }))
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.via).toBe("hosted")
  expect(result.right.channel).toBe("telegram")
  expect(result.right.config.botToken).toBeUndefined()
})

test("cx.channels.add self-host returns the Connection", async () => {
  const cx = new Caspian()
  const conn = await cx.channels.add("telegram", {
    via: "self-host",
    botToken: "tok",
    webhookUrl: "https://myapp.example.com/api/webhooks/telegram",
  })
  expect(conn.via).toBe("self-host")
  expect(conn.config.botToken).toBe("tok")
})

test("cx.channels.add hosted does not call a live gateway", async () => {
  const cx = new Caspian()
  const conn = await cx.channels.add("discord", { via: "hosted" })
  expect(conn.via).toBe("hosted")
  expect(conn.channel).toBe("discord")
})
