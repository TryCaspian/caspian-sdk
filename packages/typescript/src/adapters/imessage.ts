import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError, DecodeError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { ThreadId } from "../core/ids.ts"
import { httpLayer, type AdapterFetch } from "./http.ts"
import type { HttpJsonCall, PlannedCall } from "./plan.ts"
import { recordingLayer, skippedCommand } from "./recording.ts"
import {
  asJsonObject,
  configString,
  messageDefaults,
  encodePrefixed,
  firstHeader,
  hmacSha256Hex,
  isRecord,
  jsonObjectOf,
  suffixAfter,
  timingSafeEqualUtf8,
} from "./util.ts"

const PREFIX = "imessage:"
const DEFAULT_RELAY = "https://relay.local"

export type NativeThread = { readonly address: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.address)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  address: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

const parseRelay = (data: Record<string, unknown>): ReadonlyArray<Event> => {
  if (data.isFromMe) {
    return []
  }
  const handle = isRecord(data.handle) ? data.handle : {}
  const address = handle.address !== undefined ? String(handle.address) : ""
  return [
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ address }),
      text: typeof data.text === "string" ? data.text : "",
      chat_kind: "dm",
      sender: address,
      raw: asJsonObject(data),
    },
  ]
}

const parseSimple = (payload: Record<string, unknown>): ReadonlyArray<Event> => {
  const address = payload.from !== undefined ? String(payload.from) : ""
  return [
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ address }),
      text: typeof payload.text === "string" ? payload.text : "",
      chat_kind: "dm",
      sender: address,
      raw: asJsonObject(payload),
    },
  ]
}

export const parseIMessageUpdate = (
  raw: unknown,
): Effect.Effect<ReadonlyArray<Event>, DecodeError> => {
  const decoded = jsonObjectOf(raw)
  if (!decoded.ok) {
    return Effect.fail(decoded.error)
  }
  const payload = decoded.value
  if (payload === undefined) {
    return Effect.succeed([])
  }
  if (payload.type === "new-message" && isRecord(payload.data)) {
    return Effect.succeed(parseRelay(payload.data))
  }
  if ("from" in payload && "text" in payload) {
    return Effect.succeed(parseSimple(payload))
  }
  return Effect.succeed([])
}

const targetOf = (threadId: string): { readonly [key: string]: string } => {
  const target = decodeThreadId(threadId).address
  if (target.includes(";")) {
    return { chatGuid: target }
  }
  return { address: target }
}

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpJsonCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  const apiKey = configString(conn.config, "apiKey")
  if (apiKey.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "No apiKey in connection config",
        commandTag: command.tag,
      }),
    )
  }
  if (command.tag !== "Post") {
    return Effect.fail(
      new AdapterError({
        reason: `iMessage relay cannot execute ${command.tag}`,
        commandTag: command.tag,
      }),
    )
  }
  const base = configString(conn.config, "relayUrl") || DEFAULT_RELAY
  return Effect.succeed({
    transport: "http_json",
    method: "POST",
    url: `${base}/api/v1/message/text`,
    json: { ...targetOf(command.thread_id), message: command.text },
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    native: "sendText",
  })
}

const spec = {
  name: "imessage",
  parse: parseIMessageUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const imessageLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const imessageHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const verifyIMessage = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const secret = configString(conn.config, "webhookSecret")
  if (secret.length === 0) {
    return true
  }
  const got = firstHeader(headers, "X-Relay-Signature")
  return timingSafeEqualUtf8(hmacSha256Hex(secret, body), got)
}

export const imessage = () => ({
  name: "imessage" as const,
  parse: parseIMessageUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => ["receive", "reply", "send", "media"],
  format: (text: string): string => text,
  openModal: undefined as never,
})
