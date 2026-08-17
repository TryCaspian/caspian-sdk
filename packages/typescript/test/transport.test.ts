import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { AdapterError } from "../src/core/errors.ts"
import type { JsonObject } from "../src/core/json.ts"
import type { Sent } from "../src/core/ports.ts"
import { SmtpTransport } from "../src/interpreters/smtp.ts"
import {
  ChaosTransport,
  HttpTransport,
  MultiplexTransport,
  RecordingTransport,
} from "../src/interpreters/transport.ts"
import { VoiceResponder } from "../src/interpreters/voice.ts"

const sent = (raw: JsonObject): Sent => ({
  ok: true,
  message_id: "",
  raw,
})

test("HttpTransport posts JSON and extracts Telegram message_id", async () => {
  const posted: Array<{ url: string; body: unknown }> = []
  const http = new HttpTransport(async (url, init) => {
    posted.push({ url: String(url), body: JSON.parse(String(init?.body ?? "{}")) })
    return new Response(JSON.stringify({ ok: true, result: { message_id: 99 } }), {
      status: 200,
    })
  })
  const result = await Effect.runPromise(
    http.dispatch(
      sent({
        transport: "http_json",
        method: "POST",
        url: "https://api.telegram.org/botT/sendMessage",
        json: { chat_id: "1", text: "hi" },
        native: "sendMessage",
      }),
    ),
  )
  expect(result.message_id).toBe("99")
  expect(posted[0]?.url).toContain("sendMessage")
})

test("HttpTransport does not dispatch smtp or twiml", async () => {
  const http = new HttpTransport(async () => {
    throw new Error("should not fetch")
  })
  const result = await Effect.runPromise(
    Effect.either(http.dispatch(sent({ transport: "smtp", native: "sendmail" }))),
  )
  expect(result._tag).toBe("Left")
})

test("MultiplexTransport routes smtp and twiml", async () => {
  const emails: Array<{ to: string }> = []
  const mux = new MultiplexTransport({
    smtp: new SmtpTransport({
      sender: (message) => {
        emails.push({ to: message.to })
      },
    }),
    twiml: new VoiceResponder(),
  })
  const mail = await Effect.runPromise(
    mux.dispatch(
      sent({
        transport: "smtp",
        native: "sendmail",
        email: {
          from: "a@x",
          to: "b@x",
          subject: "hi",
          body: "body",
          in_reply_to: "",
          references: "",
          attachments: [],
        },
      }),
    ),
  )
  expect(emails).toEqual([{ to: "b@x" }])
  expect(mail.raw.native).toBe("sendmail")

  const voice = await Effect.runPromise(
    mux.dispatch(sent({ transport: "twiml", native: "say", twiml: "<Response/>" })),
  )
  expect(voice.raw.twiml).toBe("<Response/>")
})

test("ChaosTransport fails every dispatch", async () => {
  const chaos = new ChaosTransport("boom")
  const result = await Effect.runPromise(
    Effect.either(chaos.dispatch(sent({ transport: "http_json", native: "x" }))),
  )
  expect(result._tag).toBe("Left")
  if (result._tag === "Left") {
    expect(result.left).toBeInstanceOf(AdapterError)
    expect(result.left.reason).toBe("boom")
  }
})

test("RecordingTransport records and returns rec_1", async () => {
  const rec = new RecordingTransport()
  const out = await Effect.runPromise(
    rec.dispatch(sent({ transport: "http_json", native: "post" })),
  )
  expect(out.message_id).toBe("rec_1")
  expect(rec.dispatched).toHaveLength(1)
})
