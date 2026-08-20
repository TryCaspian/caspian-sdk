/**
 * Discord Gateway and Slack Socket Mode runners, driven by a fake socket.
 *
 * The TypeScript twin of test_discord_gateway.py and test_slack_socket.py. No
 * network: the socket and the URL lookup are both injected.
 */
import { describe, expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { DiscordGatewayRunner, INTENTS } from "../src/interpreters/discord_gateway.ts"
import { SlackSocketRunner } from "../src/interpreters/slack_socket.ts"
import type { GatewaySocket } from "../src/interpreters/socket.ts"

const fakeSocket = (frames: ReadonlyArray<unknown>): GatewaySocket & { sent: unknown[] } => {
  const queue = frames.map((f) => (typeof f === "string" ? f : JSON.stringify(f)))
  const sent: unknown[] = []
  return {
    sent,
    receive: async () => {
      const next = queue.shift()
      if (next === undefined) {
        throw new Error("socket drained")
      }
      return next
    },
    send: async (data: string) => {
      sent.push(JSON.parse(data))
    },
    close: () => {},
  }
}

const collect = (into: unknown[]) => (body: unknown) =>
  Effect.sync(() => {
    into.push(body)
    return [] as ReadonlyArray<unknown>
  })

// ─── Discord ────────────────────────────────────────────────────────────────

const HELLO_D = { op: 10, d: { heartbeat_interval: 45000 } }
const READY_D = {
  op: 0,
  s: 1,
  t: "READY",
  d: { session_id: "sess-1", resume_gateway_url: "wss://resume", user: { username: "b", id: "1" }, guilds: [] },
}
const messageD = (content: string, mid = "m1") => ({
  op: 0,
  s: 2,
  t: "MESSAGE_CREATE",
  d: { id: mid, channel_id: "chan-9", content, author: { id: "u1", bot: false } },
})

const discordRunner = (frames: ReadonlyArray<unknown>, sink: ReturnType<typeof collect>) => {
  const socket = fakeSocket(frames)
  const runner = new DiscordGatewayRunner("bot-token", sink, {
    open: async () => socket,
    gatewayUrl: async () => "wss://gateway.discord.gg",
  })
  return { runner, socket }
}

describe("discord gateway", () => {
  test("identifies with the message content intent", async () => {
    const seen: unknown[] = []
    const { runner, socket } = discordRunner([HELLO_D, READY_D, messageD("hi")], collect(seen))
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    const identify = socket.sent.find((f) => (f as { op: number }).op === 2) as {
      d: { token: string; intents: number }
    }
    expect(identify.d.token).toBe("bot-token")
    expect(identify.d.intents).toBe(INTENTS)
    // Without MESSAGE_CONTENT every message arrives with empty text.
    expect(identify.d.intents & (1 << 15)).toBeGreaterThan(0)
  })

  test("forwards the inner payload, not the gateway envelope", async () => {
    const seen: unknown[] = []
    const { runner } = discordRunner([HELLO_D, READY_D, messageD("hello there")], collect(seen))
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect(seen).toHaveLength(1)
    const payload = seen[0] as { content: string; op?: number; t?: string }
    expect(payload.content).toBe("hello there")
    expect(payload.op).toBeUndefined()
    expect(payload.t).toBeUndefined()
  })

  test("captures the session so a reconnect resumes instead of replaying", async () => {
    const seen: unknown[] = []
    const { runner } = discordRunner([HELLO_D, READY_D, messageD("hi")], collect(seen))
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect(runner.sessionId).toBe("sess-1")
    expect(runner.resumeUrl).toBe("wss://resume")
  })

  test("ignores dispatch types it does not handle", async () => {
    const seen: unknown[] = []
    const { runner } = discordRunner(
      [HELLO_D, READY_D, { op: 0, s: 2, t: "TYPING_START", d: { channel_id: "c" } }, messageD("real")],
      collect(seen),
    )
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect((seen[0] as { content: string }).content).toBe("real")
  })
})

// ─── Slack ──────────────────────────────────────────────────────────────────

const HELLO_S = { type: "hello" }
const eventS = (text: string, envelope = "env-1") => ({
  type: "events_api",
  envelope_id: envelope,
  payload: {
    type: "event_callback",
    event: { type: "message", text, channel: "C123", user: "U123", ts: "1.0" },
  },
})

const slackRunner = (frames: ReadonlyArray<unknown>, sink: ReturnType<typeof collect>) => {
  const socket = fakeSocket(frames)
  const runner = new SlackSocketRunner("xapp-test", sink, {
    open: async () => socket,
    openUrl: async () => "wss://wss-primary.slack.com/link",
  })
  return { runner, socket }
}

describe("slack socket mode", () => {
  test("delivers the events api payload to the sink", async () => {
    const seen: unknown[] = []
    const { runner } = slackRunner([HELLO_S, eventS("when was Delaware admitted")], collect(seen))
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect(seen).toHaveLength(1)
    expect((seen[0] as { event: { text: string } }).event.text).toBe(
      "when was Delaware admitted",
    )
  })

  test("acks the envelope before running the handler", async () => {
    // Slack redelivers anything unacked after ~3s and handlers call an LLM, so
    // acking afterwards makes the bot answer the same message twice.
    const order: string[] = []
    const socket = fakeSocket([HELLO_S, eventS("hi")])
    const originalSend = socket.send
    const tracked: GatewaySocket & { sent: unknown[] } = {
      ...socket,
      send: async (data: string) => {
        order.push("ack")
        await originalSend(data)
      },
    }
    const runner = new SlackSocketRunner(
      "xapp-test",
      () =>
        Effect.sync(() => {
          order.push("handler")
          return [] as ReadonlyArray<unknown>
        }),
      { open: async () => tracked, openUrl: async () => "wss://x" },
    )
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect(order).toEqual(["ack", "handler"])
    expect(socket.sent).toEqual([{ envelope_id: "env-1" }])
  })

  test("hello is not an event and is not acked", async () => {
    const seen: unknown[] = []
    const { runner, socket } = slackRunner([HELLO_S, eventS("real")], collect(seen))
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect(seen).toHaveLength(1)
    expect(socket.sent).toEqual([{ envelope_id: "env-1" }])
  })

  test("a malformed frame does not kill the socket", async () => {
    const seen: unknown[] = []
    const { runner } = slackRunner([HELLO_S, "not json at all", eventS("survived")], collect(seen))
    await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect((seen[0] as { event: { text: string } }).event.text).toBe("survived")
  })

  test("a bad app token is fatal rather than a reconnect loop", async () => {
    const runner = new SlackSocketRunner("xapp-bad", () => Effect.succeed([]), {
      open: async () => fakeSocket([]),
      openUrl: async () => {
        const { SlackAuthError } = await import("../src/interpreters/slack_socket.ts")
        throw new SlackAuthError("invalid_auth")
      },
    })
    const results = await Effect.runPromise(runner.run({ maxEvents: 1 }))
    expect(results).toEqual([])
  })
})
