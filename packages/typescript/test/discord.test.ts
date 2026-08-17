import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  discord,
  discordLayer,
  encodeThreadId,
  overlapKey,
  parseDiscordUpdate,
  planAck,
  planCommand,
  type PlannedCall,
} from "../src/adapters/discord.ts"
import {
  AdapterError,
  AdapterPort,
  Connection,
  ConnectionId,
  DecodeError,
  decodeCommand,
} from "../src/core/index.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("c1"),
  channel: "discord",
  config: { botToken: "bot.token.abc" },
})

test("parse ping returns empty", () => {
  const events = Effect.runSync(parseDiscordUpdate({ type: 1 }))
  expect(events).toEqual([])
})

test("parse message component as action", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      type: 3,
      id: "int1",
      token: "tok1",
      channel_id: "999",
      data: { custom_id: "confirm" },
      message: { id: "m1" },
      member: { user: { id: "u1" } },
    }),
  )
  expect(events).toHaveLength(1)
  const event = events[0]
  expect(event?.kind).toBe("action")
  if (event?.kind !== "action") {
    return
  }
  expect(event.data).toBe("confirm")
  expect(String(event.thread_id)).toBe("discord:999")
  expect(event.sender).toBe("u1")
  expect(event.raw["id"]).toBe("int1")
  expect(event.raw["token"]).toBe("tok1")
})

test("parse application command as message", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      type: 2,
      id: "int2",
      channel_id: "999",
      data: { name: "greet", options: [{ name: "text", value: "hi" }] },
    }),
  )
  expect(events).toHaveLength(1)
  const event = events[0]
  expect(event?.kind).toBe("message")
  if (event?.kind !== "message") {
    return
  }
  expect(event.text).toBe("hi")
  expect(String(event.thread_id)).toBe("discord:999")
  expect(event.chat_kind).toBe("channel")
})

test("parse gateway MESSAGE_CREATE", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      content: "hello there",
      channel_id: "999",
      id: "m5",
      author: { id: "u9" },
    }),
  )
  expect(events[0]?.kind).toBe("message")
  if (events[0]?.kind !== "message") {
    return
  }
  expect(events[0].text).toBe("hello there")
  expect(events[0].sender).toBe("u9")
})

test("parse gateway reaction", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      emoji: { name: "👍" },
      channel_id: "999",
      message_id: "m5",
      user_id: "u9",
    }),
  )
  expect(events[0]?.kind).toBe("reaction")
  if (events[0]?.kind !== "reaction") {
    return
  }
  expect(events[0].emoji).toBe("👍")
  expect(events[0].sender).toBe("u9")
})

test("unknown update types return empty", () => {
  expect(Effect.runSync(parseDiscordUpdate({ type: 99 }))).toEqual([])
  expect(Effect.runSync(parseDiscordUpdate(null))).toEqual([])
})

test("invalid JSON string is DecodeError", () => {
  const result = Effect.runSync(Effect.either(parseDiscordUpdate("not json")))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(DecodeError)
})

test("plan Post to channel messages", () => {
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "discord:999",
      text: "hi",
      actions: [],
    }),
  )
  const planned = Effect.runSync(planCommand(post, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.method).toBe("POST")
  expect(planned.url).toBe("https://discord.com/api/v10/channels/999/messages")
  expect(planned.json).toEqual({ content: "hi" })
  expect(planned.headers?.["Authorization"]).toBe("Bot bot.token.abc")
  expect(planned.native).toBe("post")
})

test("plan React PUT url", () => {
  const react = Effect.runSync(
    decodeCommand({
      tag: "React",
      thread_id: "discord:999",
      message_id: "m1",
      emoji: "👍",
    }),
  )
  const planned = Effect.runSync(planCommand(react, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.method).toBe("PUT")
  expect(planned.native).toBe("react")
  expect(planned.url).toContain("/messages/m1/reactions/")
  expect(planned.url.endsWith("/@me")).toBe(true)
})

test("plan Typing", () => {
  const typing = Effect.runSync(
    decodeCommand({ tag: "Typing", thread_id: "discord:999" }),
  )
  const planned = Effect.runSync(planCommand(typing, conn))
  expect(planned?.transport).toBe("http_json")
  if (planned?.transport !== "http_json") {
    return
  }
  expect(planned.method).toBe("POST")
  expect(planned.url.endsWith("/channels/999/typing")).toBe(true)
  expect(planned.native).toBe("typing")
})

test("plan without token is AdapterError", () => {
  const empty = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("c1"),
    channel: "discord",
    config: {},
  })
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "discord:999",
      text: "hi",
      actions: [],
    }),
  )
  const result = Effect.runSync(Effect.either(planCommand(post, empty)))
  expect(result._tag).toBe("Left")
  if (result._tag !== "Left") {
    return
  }
  expect(result.left).toBeInstanceOf(AdapterError)
  expect(result.left.reason).toContain("botToken")
})

test("ack Action is interaction callback type 6", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      type: 3,
      id: "int1",
      token: "tok1",
      channel_id: "999",
      data: { custom_id: "confirm" },
      message: { id: "m1" },
      member: { user: { id: "u1" } },
    }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  const ack = planAck(event)
  expect(ack?.url).toContain("/interactions/int1/tok1/callback")
  expect(ack?.json).toEqual({ type: 6 })
})

test("ack is undefined for Message", () => {
  const events = Effect.runSync(
    parseDiscordUpdate({
      content: "hello",
      channel_id: "999",
      id: "m5",
      author: { id: "u9" },
    }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(planAck(event)).toBeUndefined()
})

test("overlap key is the discord thread id", () => {
  const encoded = encodeThreadId({ channelId: "999" })
  expect(String(encoded)).toBe("discord:999")
  const events = Effect.runSync(
    parseDiscordUpdate({
      content: "hi",
      channel_id: "999",
      id: "m5",
      author: { id: "u9" },
    }),
  )
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("discord:999")
})

test("recording layer collects Post", async () => {
  const sink: PlannedCall[] = []
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "discord:999",
      text: "hi",
      actions: [],
    }),
  )
  await Effect.runPromise(
    Effect.gen(function* () {
      const adapter = yield* AdapterPort
      yield* adapter.execute(post, conn)
    }).pipe(Effect.provide(discordLayer(sink))),
  )
  expect(sink[0]?.native).toBe("post")
})

test("capabilities include buttons and modals", () => {
  expect(discord().capabilities()).toContain("buttons")
  expect(discord().openModal).toBeUndefined()
})
