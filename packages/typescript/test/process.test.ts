import { expect, test } from "bun:test"
import * as Schema from "effect/Schema"
import { telegramLayer, type TelegramCall } from "../src/adapters/telegram.ts"
import { Connection, ConnectionId } from "../src/core/index.ts"
import { Caspian } from "../src/facade/caspian.ts"

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
