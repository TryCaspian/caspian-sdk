import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError, DecodeError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { ThreadId } from "../core/ids.ts"
import { httpLayer, type AdapterFetch } from "./http.ts"
import type { HttpJsonCall, PlannedCall } from "./plan.ts"
import { recordingLayer, skippedCommand } from "./recording.ts"
import { capabilitiesOf } from "../catalog.ts"
import {
  asJsonObject,
  configString,
  messageDefaults,
  encodePrefixed,
  firstHeader,
  hmacSha256Base64,
  isRecord,
  jsonObjectOf,
  suffixAfter,
  timingSafeEqualUtf8,
} from "./util.ts"

const PREFIX = "x:"
const API_BASE = "https://api.twitter.com/2"

export type NativeThread = {
  readonly kind: "tweet" | "dm"
  readonly targetId: string
}

export const encodeThreadId = (native: NativeThread): ThreadId =>
  native.kind === "dm"
    ? encodePrefixed(PREFIX, `dm:${native.targetId}`)
    : encodePrefixed(PREFIX, native.targetId)

export const decodeThreadId = (
  threadId: ThreadId | string,
): NativeThread => {
  const rest = suffixAfter(String(threadId), PREFIX)
  if (rest.startsWith("dm:")) {
    return { kind: "dm", targetId: rest.slice(3) }
  }
  return { kind: "tweet", targetId: rest }
}

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

const parseDm = (dm: Record<string, unknown>): ReadonlyArray<Event> => {
  const create = isRecord(dm.message_create) ? dm.message_create : {}
  if (Object.keys(create).length === 0) {
    return []
  }
  const sender = create.sender_id !== undefined ? String(create.sender_id) : ""
  const data = isRecord(create.message_data) ? create.message_data : {}
  return [
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ kind: "dm", targetId: sender }),
      text: typeof data.text === "string" ? data.text : "",
      chat_kind: "dm",
      sender,
      raw: asJsonObject(dm),
    },
  ]
}

const parseTweet = (tweet: Record<string, unknown>): ReadonlyArray<Event> => {
  const user = isRecord(tweet.user) ? tweet.user : {}
  const userId = user.id !== undefined ? String(user.id) : ""
  return [
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ kind: "tweet", targetId: userId }),
      text: typeof tweet.text === "string" ? tweet.text : "",
      chat_kind: "channel",
      sender: userId,
      raw: asJsonObject(tweet),
    },
  ]
}

const parseSimpleDm = (dm: Record<string, unknown>): ReadonlyArray<Event> => {
  const sender = dm.from !== undefined ? String(dm.from) : ""
  return [
    {
      kind: "message",
        ...messageDefaults,
      thread_id: encodeThreadId({ kind: "dm", targetId: sender }),
      text: typeof dm.text === "string" ? dm.text : "",
      chat_kind: "dm",
      sender,
      raw: asJsonObject(dm),
    },
  ]
}

export const parseXUpdate = (
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
  const events: Event[] = []
  for (const dm of Array.isArray(payload.direct_message_events)
    ? payload.direct_message_events
    : []) {
    if (isRecord(dm)) {
      events.push(...parseDm(dm))
    }
  }
  for (const tweet of Array.isArray(payload.tweet_create_events)
    ? payload.tweet_create_events
    : []) {
    if (isRecord(tweet)) {
      events.push(...parseTweet(tweet))
    }
  }
  if (isRecord(payload.dm)) {
    events.push(...parseSimpleDm(payload.dm))
  }
  return Effect.succeed(events)
}

const headersOf = (token: string): { readonly Authorization: string } => ({
  Authorization: `Bearer ${token}`,
})

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpJsonCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  const token = configString(conn.config, "bearerToken")
  if (token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "No bearerToken in connection config",
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
  const native = decodeThreadId(command.thread_id)
  if (native.kind === "dm") {
    return Effect.succeed({
      transport: "http_json",
      method: "POST",
      url: `${API_BASE}/dm_conversations/with/${native.targetId}/messages`,
      json: { text: command.text },
      headers: headersOf(token),
      native: "createDm",
    })
  }
  return Effect.succeed({
    transport: "http_json",
    method: "POST",
    url: `${API_BASE}/tweets`,
    json: { text: command.text },
    headers: headersOf(token),
    native: "createTweet",
  })
}

const spec = {
  name: "x",
  parse: parseXUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const xLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const xHttpLayer = (fetchImpl?: AdapterFetch) => httpLayer(spec, fetchImpl)

export const verifyX = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const secret = configString(conn.config, "consumerSecret")
  if (secret.length === 0) {
    return true
  }
  const got = firstHeader(headers, "X-Twitter-Webhooks-Signature")
  return timingSafeEqualUtf8(`sha256=${hmacSha256Base64(secret, body)}`, got)
}

export const x = () => ({
  name: "x" as const,
  parse: parseXUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => capabilitiesOf("x"),
  format: (text: string): string => text,
  openModal: undefined as never,
})
