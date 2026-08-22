import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { toRequest } from "../src/hosted/outbound.ts"
import { parseDiscordUpdate } from "../src/adapters/discord/parse.ts"
import { parseEmailUpdate } from "../src/adapters/email.ts"
import { decodeCommand } from "../src/core/index.ts"
import { extractUpdates } from "../src/interpreters/polling.ts"
import { caspianHome, cliSecretPath } from "../../cli/src/credentials.ts"

test("Discord commandText includes command name when options present (H3)", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      type: 2,
      id: "int2",
      channel_id: "999",
      data: { name: "greet", options: [{ name: "text", value: "world" }] },
    })
  )
  expect(events[0]?.kind).toBe("message")
  if (events[0]?.kind === "message") {
    expect(events[0].text).toBe("/greet world")
  }
})

test("Email parseEmailUpdate rejects missing from field (M1)", () => {
  const events = Effect.runSync(
    parseEmailUpdate({
      to: "bot@caspian.dev",
      subject: "Hello",
      body: "hi there",
      message_id: "<abc@example.com>"
    })
  )
  expect(events.length).toBe(0)
})

test("Email parseEmailUpdate rejects whitespace-only from field (M1)", () => {
  const events = Effect.runSync(
    parseEmailUpdate({
      from: "   ",
      to: "bot@caspian.dev",
      subject: "Hello",
      body: "hi there",
      message_id: "<abc@example.com>"
    })
  )
  expect(events.length).toBe(0)
})

test("Typing without replyTo fails cleanly for adapter to catch (H1)", () => {
  const typing = Effect.runSync(
    decodeCommand({ tag: "Typing", thread_id: "test:1" })
  )
  const result = Effect.runSync(Effect.either(toRequest(typing)))
  expect(result._tag).toBe("Left")
})

test("extractUpdates handles non-JSON string response without crashing (M3)", () => {
  const malformedSent = {
    ok: true as const,
    message_id: "",
    raw: { body: "<html><head><title>502 Bad Gateway</title></head><body>502</body></html>" }
  }
  const updates = extractUpdates(malformedSent)
  expect(updates).toEqual([])
})

test("caspianHome preserves root directories properly on Windows and POSIX (M4)", () => {
  expect(caspianHome({ CASPIAN_HOME: "/" })).toBe("/")
  expect(caspianHome({ CASPIAN_HOME: "/custom/path/" })).toBe("/custom/path")
  expect(caspianHome({ CASPIAN_HOME: "C:\\" })).toBe("C:\\")
  expect(caspianHome({ CASPIAN_HOME: "D:\\custom\\path\\" })).toBe("D:\\custom\\path")
})

