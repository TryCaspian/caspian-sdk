import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import {
  Connection,
  ConnectionId,
  decodeCommand,
  decodeEvent,
} from "../src/core/index.ts"
import {
  decodeThreadId,
  encodeThreadId,
  executeTurn,
  overlapKey,
  parseTelegramUpdate,
  planTurn,
  telegramLayer,
  type TelegramCall,
} from "../src/adapters/telegram.ts"

const vectorsUrl = new URL(
  "../../../vectors/telegram_parse_vectors.json",
  import.meta.url,
)

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

test("golden Telegram parse vectors", async () => {
  const file = Bun.file(vectorsUrl)
  expect(await file.exists()).toBe(true)
  const vectors: unknown = await file.json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }
  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }
    const events = parseTelegramUpdate(vector.update)
    const expectedRaw = vector.expected
    expect(Array.isArray(expectedRaw), String(vector.name)).toBe(true)
    if (!Array.isArray(expectedRaw)) {
      continue
    }
    expect(events.length, String(vector.name)).toBe(expectedRaw.length)
    for (let i = 0; i < events.length; i++) {
      const decoded = Effect.runSync(Effect.either(decodeEvent(expectedRaw[i])))
      expect(decoded._tag, String(vector.name)).toBe("Right")
      if (decoded._tag !== "Right") {
        continue
      }
      const event = events[i]
      const expected = decoded.right
      expect(event?.kind, String(vector.name)).toBe(expected.kind)
      expect(String(event?.thread_id), String(vector.name)).toBe(
        String(expected.thread_id),
      )
      if (expected.kind === "message" && event?.kind === "message") {
        expect(event.text).toBe(expected.text)
        expect(event.chat_kind).toBe(expected.chat_kind)
        expect(event.sender).toBe(expected.sender)
      }
      if (expected.kind === "action" && event?.kind === "action") {
        expect(event.data).toBe(expected.data)
        expect(event.sender).toBe(expected.sender)
      }
      if (expected.kind === "reaction" && event?.kind === "reaction") {
        expect(event.emoji).toBe(expected.emoji)
        expect(event.sender).toBe(expected.sender)
      }
    }
  }
})

test("unknown update types return empty and do not throw", () => {
  expect(parseTelegramUpdate(null)).toEqual([])
  expect(parseTelegramUpdate("nope")).toEqual([])
  expect(parseTelegramUpdate({ update_id: 1 })).toEqual([])
})

test("parsed Action keeps callback_query id on raw", () => {
  const events = parseTelegramUpdate({
    update_id: 3,
    callback_query: {
      id: "cb1",
      from: { id: 42 },
      message: { message_id: 10, chat: { id: 123, type: "private" } },
      data: "done",
    },
  })
  expect(events).toHaveLength(1)
  const event = events[0]
  expect(event?.kind).toBe("action")
  expect(event?.raw["callback_query"]).toBeDefined()
})

test("encode/decode thread id and overlap key use chat", () => {
  const encoded = encodeThreadId({ chatId: "123" })
  expect(String(encoded)).toBe("telegram:123")
  expect(decodeThreadId(encoded)).toEqual({ chatId: "123" })
  const events = parseTelegramUpdate({
    update_id: 1,
    message: {
      message_id: 1,
      chat: { id: 123, type: "private" },
      text: "x",
    },
  })
  const event = events[0]
  expect(event).toBeDefined()
  if (event === undefined) {
    return
  }
  expect(overlapKey(event)).toBe("123")
})

const executeVectorsUrl = new URL(
  "../../../vectors/telegram_execute_vectors.json",
  import.meta.url,
)

test("golden Telegram execute vectors", async () => {
  const file = Bun.file(executeVectorsUrl)
  expect(await file.exists()).toBe(true)
  const vectors: unknown = await file.json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }
  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }
    const eventResult = Effect.runSync(Effect.either(decodeEvent(vector.event)))
    expect(eventResult._tag, String(vector.name)).toBe("Right")
    if (eventResult._tag !== "Right") {
      continue
    }
    expect(Array.isArray(vector.commands), String(vector.name)).toBe(true)
    if (!Array.isArray(vector.commands)) {
      continue
    }
    const commands = vector.commands.map((item, index) => {
      const decoded = Effect.runSync(Effect.either(decodeCommand(item)))
      expect(decoded._tag, `${String(vector.name)} command ${index}`).toBe(
        "Right",
      )
      return decoded._tag === "Right" ? decoded.right : undefined
    })
    if (commands.some((item) => item === undefined)) {
      continue
    }
    const calls = planTurn(
      eventResult.right,
      commands.filter((item) => item !== undefined),
    )
    expect(Array.isArray(vector.expected_calls), String(vector.name)).toBe(true)
    expect(JSON.parse(JSON.stringify(calls)), String(vector.name)).toEqual(
      vector.expected_calls,
    )
  }
})

test("Host commands are skipped so the adapter does not call Telegram", () => {
  const event = Effect.runSync(
    decodeEvent({
      kind: "message",
      thread_id: "telegram:123",
      text: "hello",
      chat_kind: "dm",
      sender: "alice",
      raw: {},
    }),
  )
  const host = Effect.runSync(
    decodeCommand({ tag: "Host", handler_id: "onMessage:0" }),
  )
  expect(planTurn(event, [host])).toEqual([])
})

test("executeTurn acks an Action then executes Post via AdapterPort", async () => {
  const calls: TelegramCall[] = []
  const events = parseTelegramUpdate({
    update_id: 3,
    callback_query: {
      id: "cb1",
      from: { id: 42 },
      message: { message_id: 10, chat: { id: 123, type: "private" } },
      data: "done",
    },
  })
  const event = events[0]
  expect(event?.kind).toBe("action")
  if (event === undefined) {
    return
  }
  const post = Effect.runSync(
    decodeCommand({
      tag: "Post",
      thread_id: "telegram:123",
      text: "ok",
      actions: [],
    }),
  )
  const conn = Schema.decodeUnknownSync(Connection)({
    id: Schema.decodeUnknownSync(ConnectionId)("conn-1"),
    channel: "telegram",
    config: {},
  })
  await Effect.runPromise(
    executeTurn(event, [post], conn).pipe(Effect.provide(telegramLayer(calls))),
  )
  expect(calls.map((call) => call.method)).toEqual([
    "answerCallbackQuery",
    "sendMessage",
  ])
})


