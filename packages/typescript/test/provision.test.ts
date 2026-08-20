/**
 * Provisioning options: the same names and rules as the Python SDK, so docs
 * are written once. Public keys are snake_case (`bot_token`), camelCase is
 * tolerated, and the stored config is camelCase because that is what the
 * adapters read.
 */
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { Caspian } from "../src/facade/caspian.ts"
import { addChannel } from "../src/provision/add.ts"

const runEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

test("via is required — omitting it is DecodeError, not hosted", () => {
  const result = runEither(addChannel("telegram", {}))
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect((result.left as { _tag: string })._tag).toBe("DecodeError")
})

test("via must be hosted or self-host — not oauth, not credentials", () => {
  const oauth = runEither(addChannel("slack", { via: "oauth" }))
  const creds = runEither(addChannel("slack", { via: "credentials" }))
  expect(Either.isLeft(oauth)).toBe(true)
  expect(Either.isLeft(creds)).toBe(true)
})

test("self-host without a bot token is ProvisionError", () => {
  const result = runEither(addChannel("telegram", { via: "self-host" }))
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect((result.left as { _tag: string })._tag).toBe("ProvisionError")
})

test("snake_case options work and are stored camelCase for the adapters", () => {
  // The Python surface: cx.channels.add("slack", via="self-host",
  // bot_token=..., signing_secret=...). Same strings must work here.
  const result = runEither(
    addChannel("slack", {
      via: "self-host",
      bot_token: "xoxb-tok",
      signing_secret: "sec",
      app_token: "xapp-tok",
    }),
  )
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.config.botToken).toBe("xoxb-tok")
  expect(result.right.config.signingSecret).toBe("sec")
  expect(result.right.config.appToken).toBe("xapp-tok")
})

test("camelCase input is tolerated", () => {
  const result = runEither(
    addChannel("telegram", { via: "self-host", botToken: "tok" }),
  )
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.config.botToken).toBe("tok")
})

test("no webhook url is required — socket channels have none", () => {
  // Discord's gateway and Slack's Socket Mode receive over a held-open
  // socket. A mandatory webhookUrl made those unprovisionable.
  const result = runEither(
    addChannel("discord", { via: "self-host", bot_token: "tok" }),
  )
  expect(Either.isRight(result)).toBe(true)
})

test("unknown keys pass through, as in Python", () => {
  // Adapters define what credentials they need; provisioning does not keep a
  // parallel list. account_sid reaches the sms adapter untouched in spirit.
  const result = runEither(
    addChannel("sms", { via: "self-host", bot_token: "t", account_sid: "AC1" }),
  )
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.config.accountSid).toBe("AC1")
})

test("inbound defaults to true and can be turned off", () => {
  const on = runEither(addChannel("telegram", { via: "self-host", bot_token: "t" }))
  const off = runEither(
    addChannel("telegram", { via: "self-host", bot_token: "t", inbound: false }),
  )
  expect(Either.isRight(on) && on.right.config.inbound).toBe(true)
  expect(Either.isRight(off) && off.right.config.inbound).toBe(false)
})

test("hosted accepts credentials to forward, as Python does", () => {
  // Hosted Telegram is BYO token even in Python: the gateway owns inbound
  // I/O but does not mint a bot. Options flow through to the connect body.
  const result = runEither(
    addChannel("telegram", { via: "hosted", bot_token: "tok" }),
  )
  expect(Either.isRight(result)).toBe(true)
  if (Either.isLeft(result)) {
    return
  }
  expect(result.right.via).toBe("hosted")
  expect(result.right.config.botToken).toBe("tok")
})

test("cx.channels.add self-host returns the Connection", async () => {
  const cx = new Caspian()
  const conn = await cx.channels.add("telegram", {
    via: "self-host",
    bot_token: "tok",
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
