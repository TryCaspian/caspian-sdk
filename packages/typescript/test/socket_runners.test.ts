/**
 * Discord Gateway and Slack Socket Mode, driven by SocketSession + a fake socket.
 *
 * The TypeScript twin of test_discord_gateway.py and test_slack_socket.py. No
 * network: the socket and the URL lookup are both injected.
 */
import { describe, expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { DiscordSocket, INTENTS } from "../src/adapters/discord/socket.ts"
import { SlackSocket } from "../src/adapters/slack/socket.ts"
import type { JsonObject } from "../src/core/json.ts"
import type { Sent } from "../src/core/ports.ts"
import { SocketSession, type GatewaySocket } from "../src/interpreters/socket.ts"
import type { Transport } from "../src/interpreters/transport.ts"

const fakeSocket = (
  frames: ReadonlyArray<unknown>,
): GatewaySocket & { sent: unknown[]; queue: string[] } => {
  const queue = frames.map((f) => (typeof f === "string" ? f : JSON.stringify(f)))
  const sent: unknown[] = []
  return {
    sent,
    queue,
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

const urlTransport = (response: JsonObject): Transport => ({
  dispatch: (_sent: Sent) =>
    Effect.succeed({
      ok: true as const,
      message_id: "",
      raw: { response },
    }),
})

// ─── Discord ────────────────────────────────────────────────────────────────

const HELLO_D = { op: 10, d: { heartbeat_interval: 45000 } }
const READY_D = {
  op: 0,
  s: 1,
  t: "READY",
  d: {
    session_id: "sess-1",
    resume_gateway_url: "wss://resume",
    user: { username: "b", id: "1" },
    guilds: [],
  },
}
const messageD = (content: string, mid = "m1") => ({
  op: 0,
  s: 2,
  t: "MESSAGE_CREATE",
  d: { id: mid, channel_id: "chan-9", content, author: { id: "u1", bot: false } },
})

const discordSession = (frames: ReadonlyArray<unknown>, sink: ReturnType<typeof collect>) => {
  const socket = fakeSocket(frames)
  const session = new SocketSession(new DiscordSocket("bot-token"), sink, {
    open: async () => socket,
    transport: urlTransport({ url: "wss://gateway.discord.gg" }),
  })
  return { session, socket }
}

describe("discord gateway", () => {
  test("identifies with the message content intent", async () => {
    const seen: unknown[] = []
    const { session, socket } = discordSession(
      [HELLO_D, READY_D, messageD("hi")],
      collect(seen),
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    const identify = socket.sent.find((f) => (f as { op: number }).op === 2) as {
      d: { token: string; intents: number }
    }
    expect(identify.d.token).toBe("bot-token")
    expect(identify.d.intents).toBe(INTENTS)
    expect(identify.d.intents & (1 << 15)).toBeGreaterThan(0)
  })

  test("forwards the inner payload, not the gateway envelope", async () => {
    const seen: unknown[] = []
    const { session } = discordSession(
      [HELLO_D, READY_D, messageD("hello there")],
      collect(seen),
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect(seen).toHaveLength(1)
    const payload = seen[0] as { content: string; op?: number; t?: string }
    expect(payload.content).toBe("hello there")
    expect(payload.op).toBeUndefined()
    expect(payload.t).toBeUndefined()
  })

  test("reconnects with RESUME after a drop", async () => {
    const seen: unknown[] = []
    const { session, socket } = discordSession(
      [HELLO_D, READY_D, messageD("hi")],
      collect(seen),
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    session.restart()
    socket.queue.push(JSON.stringify(HELLO_D), JSON.stringify(messageD("again", "m2")))
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect(socket.sent.some((f) => (f as { op: number }).op === 6)).toBe(true)
    const resume = socket.sent.find((f) => (f as { op: number }).op === 6) as {
      d: { session_id: string }
    }
    expect(resume.d.session_id).toBe("sess-1")
  })

  test("ignores dispatch types it does not handle", async () => {
    const seen: unknown[] = []
    const { session } = discordSession(
      [HELLO_D, READY_D, { op: 0, s: 2, t: "TYPING_START", d: { channel_id: "c" } }, messageD("real")],
      collect(seen),
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
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

const slackSession = (
  frames: ReadonlyArray<unknown>,
  sink: ReturnType<typeof collect>,
  response: JsonObject = { ok: true, url: "wss://wss-primary.slack.com/link" },
) => {
  const socket = fakeSocket(frames)
  const session = new SocketSession(new SlackSocket("xapp-test"), sink, {
    open: async () => socket,
    transport: urlTransport(response),
  })
  return { session, socket }
}

describe("slack socket mode", () => {
  test("delivers the events api payload to the sink", async () => {
    const seen: unknown[] = []
    const { session } = slackSession(
      [HELLO_S, eventS("when was Delaware admitted")],
      collect(seen),
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect(seen).toHaveLength(1)
    expect((seen[0] as { event: { text: string } }).event.text).toBe(
      "when was Delaware admitted",
    )
  })

  test("acks the envelope before running the handler", async () => {
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
    const session = new SocketSession(
      new SlackSocket("xapp-test"),
      () =>
        Effect.sync(() => {
          order.push("handler")
          return [] as ReadonlyArray<unknown>
        }),
      {
        open: async () => tracked,
        transport: urlTransport({ ok: true, url: "wss://x" }),
      },
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect(order).toEqual(["ack", "handler"])
    expect(socket.sent).toEqual([{ envelope_id: "env-1" }])
  })

  test("hello is not an event and is not acked", async () => {
    const seen: unknown[] = []
    const { session, socket } = slackSession([HELLO_S, eventS("real")], collect(seen))
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect(seen).toHaveLength(1)
    expect(socket.sent).toEqual([{ envelope_id: "env-1" }])
  })

  test("a malformed frame does not kill the socket", async () => {
    const seen: unknown[] = []
    const { session } = slackSession(
      [HELLO_S, "not json at all", eventS("survived")],
      collect(seen),
    )
    await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect((seen[0] as { event: { text: string } }).event.text).toBe("survived")
  })

  test("a bad app token is fatal rather than a reconnect loop", async () => {
    const session = new SocketSession(new SlackSocket("xapp-bad"), () => Effect.succeed([]), {
      open: async () => fakeSocket([]),
      transport: urlTransport({ ok: false, error: "invalid_auth" }),
    })
    const results = await Effect.runPromise(session.run({ maxEvents: 1 }))
    expect(results).toEqual([])
  })
})
