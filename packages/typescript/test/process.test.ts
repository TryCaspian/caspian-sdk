import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import * as Schema from "effect/Schema"
import {
  telegramHttpLayer,
  telegramLayer,
  type TelegramCall,
  type TelegramFetch,
} from "../src/adapters/telegram.ts"
import {
  AdapterPort,
  Connection,
  ConnectionId,
  DecodeError,
} from "../src/core/index.ts"
import { Caspian } from "../src/facade/caspian.ts"
import { makeProcessInterpreter } from "../src/interpreters/process.ts"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("conn-1"),
  channel: "telegram",
  config: { botToken: "fake-token" },
})

const telegramRequest = (
  body: unknown,
  secret?: string,
): Request => {
  const headers = new Headers({ "content-type": "application/json" })
  if (secret !== undefined) {
    headers.set("X-Telegram-Bot-Api-Secret-Token", secret)
  }
  return new Request("https://bot.example.com/api/webhooks/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  })
}

const dmUpdate = {
  update_id: 1,
  message: {
    message_id: 10,
    from: { id: 42, username: "alice" },
    chat: { id: 123, type: "private" },
    text: "hello",
  },
}

const actionUpdate = {
  update_id: 3,
  callback_query: {
    id: "cb1",
    from: { id: 42, username: "alice" },
    message: {
      message_id: 10,
      chat: { id: 123, type: "private" },
    },
    data: "done",
  },
}

const listenEcho = async (calls: TelegramCall[], secret = "s3cret") => {
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  cx.onAction({ channel: "telegram" }, async (thread) => {
    await thread.post("ok")
  })
  await cx.listen({
    channel: "telegram",
    secretToken: secret,
    connection: conn,
    adapter: telegramLayer(calls),
  })
  return cx
}

test("telegram webhook ACKs 200 and records sendMessage", async () => {
  const calls: TelegramCall[] = []
  const cx = await listenEcho(calls)
  const response = await cx.webhooks.telegram(telegramRequest(dmUpdate, "s3cret"))
  expect(response.status).toBe(200)
  const post = calls.find((call) => call.method === "sendMessage")
  expect(post?.body).toEqual({ chat_id: "123", text: "echo:hello" })
})

test("wrong secret is 401 and does not execute", async () => {
  const calls: TelegramCall[] = []
  const cx = await listenEcho(calls)
  const response = await cx.webhooks.telegram(
    telegramRequest(dmUpdate, "nope"),
  )
  expect(response.status).toBe(401)
  expect(calls).toEqual([])
})

test("unknown update still ACKs 200 and does not execute", async () => {
  const calls: TelegramCall[] = []
  const cx = await listenEcho(calls)
  const response = await cx.webhooks.telegram(
    telegramRequest({ update_id: 9, my_chat_member: { chat: { id: 1 } } }, "s3cret"),
  )
  expect(response.status).toBe(200)
  expect(calls).toEqual([])
})

test("host failure still ACKs 200 so Telegram does not retry", async () => {
  const calls: TelegramCall[] = []
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async () => {
    throw new Error("boom")
  })
  await cx.listen({
    channel: "telegram",
    secretToken: "s3cret",
    connection: conn,
    adapter: telegramLayer(calls),
  })
  const response = await cx.webhooks.telegram(telegramRequest(dmUpdate, "s3cret"))
  expect(response.status).toBe(200)
})

test("Action turn acks callback then posts", async () => {
  const calls: TelegramCall[] = []
  const cx = await listenEcho(calls)
  const response = await cx.webhooks.telegram(
    telegramRequest(actionUpdate, "s3cret"),
  )
  expect(response.status).toBe(200)
  expect(calls[0]?.method).toBe("answerCallbackQuery")
  expect(calls.some((call) => call.method === "sendMessage")).toBe(true)
})

test("webhooks.telegram before listen fails", async () => {
  const cx = new Caspian()
  await expect(
    cx.webhooks.telegram(telegramRequest(dmUpdate, "s3cret")),
  ).rejects.toThrow(/listen/)
})

test("process does not default a Telegram secret header", async () => {
  const calls: TelegramCall[] = []
  const process = await Effect.runPromise(
    makeProcessInterpreter(
      { rules: [] },
      {
        connection: conn,
        adapter: telegramLayer(calls),
        secretToken: "s3cret",
      },
    ),
  )
  const response = await Effect.runPromise(
    process.handle(telegramRequest(dmUpdate, "s3cret")),
  )
  expect(response.status).toBe(401)
  expect(calls).toEqual([])
})

test("handle verifies the secretHeader passed with the request", async () => {
  const calls: TelegramCall[] = []
  const process = await Effect.runPromise(
    makeProcessInterpreter(
      { rules: [] },
      {
        connection: conn,
        adapter: telegramLayer(calls),
        secretToken: "s3cret",
      },
    ),
  )
  const response = await Effect.runPromise(
    process.handle(telegramRequest(dmUpdate, "s3cret"), {
      secretHeader: "X-Telegram-Bot-Api-Secret-Token",
    }),
  )
  expect(response.status).toBe(200)
})

test("parse DecodeError still ACKs 200 so Telegram does not retry", async () => {
  const failing = Layer.succeed(AdapterPort, {
    name: "fail",
    parse: () => Effect.fail(new DecodeError({ reason: "bad inbound" })),
    overlapKey: (event) => String(event.thread_id),
    verify: () => true,
    acknowledge: () =>
      Effect.succeed({ ok: true as const, message_id: "", raw: {} }),
    execute: () => Effect.succeed({ ok: true as const, message_id: "", raw: {} }),
    capabilities: () => ["receive"],
    format: (text) => text,
  })
  const cx = new Caspian()
  await cx.listen({
    channel: "telegram",
    secretToken: "s3cret",
    connection: conn,
    adapter: failing,
  })
  const response = await cx.webhooks.telegram(telegramRequest({}, "s3cret"))
  expect(response.status).toBe(200)
})

test("http layer posts planned calls to api.telegram.org", async () => {
  const posted: Array<{ url: string; body: unknown }> = []
  const fetchImpl: TelegramFetch = async (url, init) => {
    posted.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")),
    })
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  await cx.listen({
    channel: "telegram",
    secretToken: "s3cret",
    connection: conn,
    adapter: telegramHttpLayer(fetchImpl),
  })
  const response = await cx.webhooks.telegram(
    telegramRequest(dmUpdate, "s3cret"),
  )
  expect(response.status).toBe(200)
  const send = posted.find((item) => item.url.includes("sendMessage"))
  expect(send?.url).toBe(
    "https://api.telegram.org/botfake-token/sendMessage",
  )
  expect(send?.body).toEqual({ chat_id: "123", text: "echo:hello" })
})
