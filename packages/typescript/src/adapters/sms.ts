import { Buffer } from "node:buffer"
import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError, DecodeError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { ThreadId } from "../core/ids.ts"
import { httpLayer, type AdapterFetch } from "./http.ts"
import type { HttpFormCall, PlannedCall } from "./plan.ts"
import { recordingLayer, skippedCommand } from "./recording.ts"
import { capabilitiesOf } from "../catalog.ts"
import {
  configString,
  messageDefaults,
  encodePrefixed,
  firstHeader,
  formFieldsOf,
  hmacSha1Base64,
  suffixAfter,
  timingSafeEqualUtf8,
} from "./util.ts"

const PREFIX = "sms:"
const API_BASE = "https://api.twilio.com/2010-04-01"

export type NativeThread = { readonly number: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.number)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  number: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

export const parseSmsUpdate = (
  raw: unknown,
): Effect.Effect<ReadonlyArray<Event>, DecodeError> => {
  const form = formFieldsOf(raw)
  if (!form.ok) {
    return Effect.fail(form.error)
  }
  const fromNumber = form.value.From ?? ""
  if (fromNumber.length === 0) {
    return Effect.succeed([])
  }
  return Effect.succeed([
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ number: fromNumber }),
      text: form.value.Body ?? "",
      chat_kind: "dm",
      sender: fromNumber,
      raw: form.value,
    },
  ])
}

const basicAuth = (sid: string, token: string): string =>
  `Basic ${Buffer.from(`${sid}:${token}`).toString("base64")}`

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpFormCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  const sid = configString(conn.config, "accountSid")
  const token = configString(conn.config, "authToken")
  if (sid.length === 0 || token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "No accountSid/authToken in connection config",
        commandTag: command.tag,
      }),
    )
  }
  if (command.tag !== "Post") {
    return Effect.fail(
      new AdapterError({
        reason: `Unsupported command: ${command.tag}`,
        commandTag: command.tag,
      }),
    )
  }
  const fromNumber = configString(conn.config, "fromNumber")
  const call: HttpFormCall = {
    transport: "http_form",
    method: "POST",
    url: `${API_BASE}/Accounts/${sid}/Messages.json`,
    form: {
      To: decodeThreadId(command.thread_id).number,
      From: fromNumber,
      Body: command.text,
    },
    headers: { Authorization: basicAuth(sid, token) },
    native: "sendMessage",
  }
  return Effect.succeed(call)
}

const spec = {
  name: "sms",
  parse: parseSmsUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const smsLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const smsHttpLayer = (fetchImpl?: AdapterFetch) => httpLayer(spec, fetchImpl)

export const verifySms = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const authToken = configString(conn.config, "authToken")
  const webhookUrl = configString(conn.config, "webhookUrl")
  if (authToken.length === 0 || webhookUrl.length === 0) {
    return true
  }
  const signature = firstHeader(headers, "X-Twilio-Signature")
  const params = new URLSearchParams(body)
  const keys = [...params.keys()].sort()
  let payload = webhookUrl
  for (const key of keys) {
    for (const value of params.getAll(key)) {
      payload += key + value
    }
  }
  return timingSafeEqualUtf8(hmacSha1Base64(authToken, payload), signature)
}

export const sms = () => ({
  name: "sms" as const,
  parse: parseSmsUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => capabilitiesOf("sms"),
  format: (text: string): string => text,
  openModal: undefined as never,
})
