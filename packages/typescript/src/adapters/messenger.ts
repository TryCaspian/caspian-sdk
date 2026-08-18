import * as Effect from "effect/Effect"
import type { Command, PostAction } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError, DecodeError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { ThreadId } from "../core/ids.ts"
import { httpLayer, type AdapterFetch } from "./http.ts"
import type { HttpJsonCall, PlannedCall } from "./plan.ts"
import { recordingLayer, skippedCommand } from "./recording.ts"
import {
  asJsonObject,
  actionDefaults,
  buttonData,
  buttonText,
  configString,
  encodePrefixed,
  firstHeader,
  hmacSha256Hex,
  isRecord,
  jsonObjectOf,
  messageDefaults,
  suffixAfter,
  timingSafeEqualUtf8,
} from "./util.ts"

const PREFIX = "messenger:"
const SEND_URL = "https://graph.facebook.com/v21.0/me/messages"

export type NativeThread = { readonly psid: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.psid)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  psid: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

const asList = (value: unknown): ReadonlyArray<unknown> =>
  Array.isArray(value) ? value : []

const parseMessaging = (m: Record<string, unknown>): ReadonlyArray<Event> => {
  const senderObj = isRecord(m.sender) ? m.sender : {}
  const sender = senderObj.id !== undefined ? String(senderObj.id) : ""
  const threadId = encodeThreadId({ psid: sender })
  if ("postback" in m) {
    const pb = isRecord(m.postback) ? m.postback : {}
    return [
      {
        kind: "action",
          ...actionDefaults,
        thread_id: threadId,
        data: typeof pb.payload === "string" ? pb.payload : "",
        sender,
        raw: asJsonObject(m),
      },
    ]
  }
  if ("message" in m) {
    const msg = isRecord(m.message) ? m.message : {}
    return [
      {
        kind: "message",
          ...messageDefaults,
        thread_id: threadId,
        text: typeof msg.text === "string" ? msg.text : "",
        chat_kind: "dm",
        sender,
        raw: asJsonObject(m),
      },
    ]
  }
  return []
}

export const parseMessengerUpdate = (
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
  for (const entry of asList(payload.entry)) {
    if (!isRecord(entry)) {
      continue
    }
    for (const item of asList(entry.messaging)) {
      if (isRecord(item)) {
        events.push(...parseMessaging(item))
      }
    }
  }
  return Effect.succeed(events)
}

const req = (
  token: string,
  body: { readonly [key: string]: unknown },
  native: string,
): HttpJsonCall => ({
  transport: "http_json",
  method: "POST",
  url: SEND_URL,
  json: body,
  headers: { Authorization: `Bearer ${token}` },
  native,
})

const messageBody = (
  tid: string,
  text: string,
  actions: ReadonlyArray<PostAction>,
): { readonly [key: string]: unknown } => {
  const message: { [key: string]: unknown } = { text }
  if (actions.length > 0) {
    message.quick_replies = actions.slice(0, 13).map((action) => ({
      content_type: "text",
      title: buttonText(action),
      payload: buttonData(action) || buttonText(action),
    }))
  }
  return { recipient: { id: decodeThreadId(tid).psid }, message }
}

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpJsonCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  const token = configString(conn.config, "pageAccessToken")
  if (token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "No pageAccessToken in connection config",
        commandTag: command.tag,
      }),
    )
  }
  switch (command.tag) {
    case "Post":
      return Effect.succeed(
        req(token, messageBody(command.thread_id, command.text, command.actions), "message"),
      )
    case "Typing":
      return Effect.succeed(
        req(
          token,
          {
            recipient: { id: decodeThreadId(command.thread_id).psid },
            sender_action: "typing_on",
          },
          "typing_on",
        ),
      )
    default:
      return Effect.fail(
        new AdapterError({
          reason: `Messenger does not support ${command.tag}`,
          commandTag: command.tag,
        }),
      )
  }
}

const spec = {
  name: "messenger",
  parse: parseMessengerUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const messengerLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const messengerHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const verifyMessenger = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const secret = configString(conn.config, "appSecret")
  if (secret.length === 0) {
    return true
  }
  const got = firstHeader(headers, "X-Hub-Signature-256")
  return timingSafeEqualUtf8(`sha256=${hmacSha256Hex(secret, body)}`, got)
}

export const messenger = () => ({
  name: "messenger" as const,
  parse: parseMessengerUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => [
    "receive",
    "reply",
    "send",
    "media",
    "buttons",
    "typing",
  ],
  format: (text: string): string => text,
  openModal: undefined as never,
})
