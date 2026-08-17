import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError, DecodeError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { ThreadId } from "../core/ids.ts"
import { httpLayer, type AdapterFetch } from "./http.ts"
import type { PlannedCall, TwimlCall } from "./plan.ts"
import { recordingLayer, skippedCommand } from "./recording.ts"
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

const PREFIX = "voice:"

export type NativeThread = { readonly callSid: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.callSid)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  callSid: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

export const parseVoiceUpdate = (
  raw: unknown,
): Effect.Effect<ReadonlyArray<Event>, DecodeError> => {
  const form = formFieldsOf(raw)
  if (!form.ok) {
    return Effect.fail(form.error)
  }
  const callSid = form.value.CallSid ?? ""
  if (callSid.length === 0) {
    return Effect.succeed([])
  }
  const text = form.value.SpeechResult || form.value.TranscriptionText || ""
  return Effect.succeed([
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ callSid }),
      text,
      chat_kind: "dm",
      sender: form.value.From ?? "",
      raw: form.value,
    },
  ])
}

const escapeXml = (text: string): string =>
  text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;")

const say = (text: string): TwimlCall => ({
  transport: "twiml",
  native: "say",
  twiml: `<?xml version="1.0" encoding="UTF-8"?><Response><Say>${escapeXml(text)}</Say></Response>`,
})

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<TwimlCall | undefined, AdapterError> => {
  void conn
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  if (command.tag === "Post") {
    return Effect.succeed(say(command.text))
  }
  return Effect.fail(
    new AdapterError({
      reason: `Unsupported command: ${command.tag}`,
      commandTag: command.tag,
    }),
  )
}

const spec = {
  name: "voice",
  parse: parseVoiceUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const voiceLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const voiceHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const verifyVoice = (
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

export const voice = () => ({
  name: "voice" as const,
  parse: parseVoiceUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => ["receive", "send", "voice", "tts"],
  format: escapeXml,
  openModal: undefined as never,
})
