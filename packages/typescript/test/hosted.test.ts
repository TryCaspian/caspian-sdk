import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import { Connection, ConnectionId } from "../src/core/index.ts"
import { Caspian } from "../src/facade/caspian.ts"
import {
  hostedHttpLayer,
  hostedLayer,
  makeHostedInterpreter,
  type HostedCall,
  type HostedFetch,
} from "../src/interpreters/hosted.ts"

const SECRET = "whsec_test"
const HEADER = "X-Caspian-Signature"

const conn = Schema.decodeUnknownSync(Connection)({
  id: Schema.decodeUnknownSync(ConnectionId)("conn-hosted"),
  channel: "telegram",
  config: { apiKey: "ck_test" },
})

const dmEvent = {
  kind: "message" as const,
  thread_id: "telegram:123",
  text: "hello",
  chat_kind: "dm" as const,
  sender: "alice",
  raw: {},
}

const delivery = { event: dmEvent }

const hex = (bytes: ArrayBuffer): string =>
  [...new Uint8Array(bytes)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")

const signBody = async (secret: string, body: string): Promise<string> => {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  )
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(body),
  )
  return `sha256=${hex(mac)}`
}

const caspianRequest = async (
  body: unknown,
  secret = SECRET,
): Promise<Request> => {
  const payload = JSON.stringify(body)
  const headers = new Headers({ "content-type": "application/json" })
  headers.set(HEADER, await signBody(secret, payload))
  return new Request("https://maya.example.com/api/caspian", {
    method: "POST",
    headers,
    body: payload,
  })
}

const runEcho = async (calls: HostedCall[]) => {
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  await cx.run({
    channel: "telegram",
    webhookSecret: SECRET,
    connection: conn,
    adapter: hostedLayer(calls),
  })
  return cx
}

test("caspian webhook ACKs 200 and records Post on the outbox", async () => {
  const calls: HostedCall[] = []
  const cx = await runEcho(calls)
  const response = await cx.webhooks.caspian(await caspianRequest(delivery))
  expect(response.status).toBe(200)
  const post = calls.find(
    (call) => call.op === "execute" && call.command.tag === "Post",
  )
  expect(post?.op).toBe("execute")
  if (post?.op === "execute" && post.command.tag === "Post") {
    expect(post.command.text).toBe("echo:hello")
  }
})

test("wrong HMAC is 401 and does not execute", async () => {
  const calls: HostedCall[] = []
  const cx = await runEcho(calls)
  const response = await cx.webhooks.caspian(
    await caspianRequest(delivery, "whsec_other"),
  )
  expect(response.status).toBe(401)
  expect(calls).toEqual([])
})

test("missing signature is 401", async () => {
  const calls: HostedCall[] = []
  const cx = await runEcho(calls)
  const response = await cx.webhooks.caspian(
    new Request("https://maya.example.com/api/caspian", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(delivery),
    }),
  )
  expect(response.status).toBe(401)
  expect(calls).toEqual([])
})

test("hosted interpreter does not default a Caspian signature header", async () => {
  const calls: HostedCall[] = []
  const hosted = await Effect.runPromise(
    makeHostedInterpreter(
      { rules: [] },
      {
        connection: conn,
        adapter: hostedLayer(calls),
        webhookSecret: SECRET,
      },
    ),
  )
  const response = await Effect.runPromise(
    hosted.handle(await caspianRequest(delivery)),
  )
  expect(response.status).toBe(401)
  expect(calls).toEqual([])
})

test("handle verifies the signatureHeader passed with the request", async () => {
  const calls: HostedCall[] = []
  const hosted = await Effect.runPromise(
    makeHostedInterpreter(
      { rules: [] },
      {
        connection: conn,
        adapter: hostedLayer(calls),
        webhookSecret: SECRET,
      },
    ),
  )
  const response = await Effect.runPromise(
    hosted.handle(await caspianRequest(delivery), {
      signatureHeader: HEADER,
    }),
  )
  expect(response.status).toBe(200)
})

test("host failure still ACKs 200 so the gateway does not retry", async () => {
  const calls: HostedCall[] = []
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async () => {
    throw new Error("boom")
  })
  await cx.run({
    channel: "telegram",
    webhookSecret: SECRET,
    connection: conn,
    adapter: hostedLayer(calls),
  })
  const response = await cx.webhooks.caspian(await caspianRequest(delivery))
  expect(response.status).toBe(200)
})

test("malformed delivery still ACKs 200 and does not execute", async () => {
  const calls: HostedCall[] = []
  const cx = await runEcho(calls)
  const response = await cx.webhooks.caspian(
    await caspianRequest({ nope: true }),
  )
  expect(response.status).toBe(200)
  expect(calls).toEqual([])
})

test("webhooks.caspian before run fails", async () => {
  const cx = new Caspian()
  await expect(
    cx.webhooks.caspian(await caspianRequest(delivery)),
  ).rejects.toThrow(/run/)
})

test("http layer posts commands to the Caspian outbox", async () => {
  const posted: Array<{ url: string; body: unknown; auth: string | null }> = []
  const fetchImpl: HostedFetch = async (url, init) => {
    posted.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")),
      auth: new Headers(init?.headers).get("authorization"),
    })
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  const cx = new Caspian()
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  await cx.run({
    channel: "telegram",
    webhookSecret: SECRET,
    connection: conn,
    adapter: hostedHttpLayer(fetchImpl),
  })
  const response = await cx.webhooks.caspian(await caspianRequest(delivery))
  expect(response.status).toBe(200)
  const send = posted.find((item) => {
    const body = item.body
    return (
      typeof body === "object" &&
      body !== null &&
      "op" in body &&
      body.op === "execute" &&
      "command" in body &&
      typeof body.command === "object" &&
      body.command !== null &&
      "tag" in body.command &&
      body.command.tag === "Post"
    )
  })
  expect(send?.url).toBe("https://api.trycaspianai.com/v1/outbox")
  expect(send?.auth).toBe("Bearer ck_test")
  expect(send?.body).toEqual({
    op: "execute",
    command: {
      tag: "Post",
      thread_id: "telegram:123",
      text: "echo:hello",
      actions: [],
      standalone: false,
    },
  })
})
