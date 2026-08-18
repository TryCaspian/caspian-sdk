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
  buttonData,
  buttonText,
  configString,
  encodePrefixed,
  firstHeader,
  hmacSha256Hex,
  isRecord,
  jsonObjectOf,
  messageDefaults,
  reactionDefaults,
  suffixAfter,
  timingSafeEqualUtf8,
} from "./util.ts"

const PREFIX = "whatsapp:"
const GRAPH_BASE = "https://graph.facebook.com/v21.0"

export type NativeThread = { readonly waId: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.waId)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  waId: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

const asList = (value: unknown): ReadonlyArray<unknown> =>
  Array.isArray(value) ? value : []

const parseMessage = (msg: Record<string, unknown>): ReadonlyArray<Event> => {
  const waId = msg.from !== undefined ? String(msg.from) : ""
  const threadId = encodeThreadId({ waId })
  const msgType = msg.type
  if (msgType === "reaction") {
    const reaction = isRecord(msg.reaction) ? msg.reaction : {}
    return [
      {
        kind: "reaction",
        ...reactionDefaults,
        thread_id: threadId,
        emoji: typeof reaction.emoji === "string" ? reaction.emoji : "",
        sender: waId,
        raw: asJsonObject(msg),
      },
    ]
  }
  const text =
    msgType === "text" && isRecord(msg.text) && typeof msg.text.body === "string"
      ? msg.text.body
      : ""
  return [
    {
      kind: "message",
        ...messageDefaults,
      thread_id: threadId,
      text,
      chat_kind: "dm",
      sender: waId,
      raw: asJsonObject(msg),
    },
  ]
}

export const parseWhatsAppUpdate = (
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
    for (const change of asList(entry.changes)) {
      const value = isRecord(change) && isRecord(change.value) ? change.value : {}
      for (const msg of asList(value.messages)) {
        if (isRecord(msg)) {
          events.push(...parseMessage(msg))
        }
      }
      for (const status of asList(value.statuses)) {
        if (!isRecord(status)) {
          continue
        }
        const state = status.status
        if (state !== "read" && state !== "delivered") {
          continue
        }
        const waId =
          status.recipient_id !== undefined ? String(status.recipient_id) : ""
        events.push({
          kind: "receipt",
          thread_id: encodeThreadId({ waId }),
          status: state,
          sender: waId,
          message_id: status.id !== undefined ? String(status.id) : "",
          raw: asJsonObject(status),
        })
      }
    }
  }
  return Effect.succeed(events)
}

const req = (
  url: string,
  token: string,
  body: { readonly [key: string]: unknown },
  native: string,
): HttpJsonCall => ({
  transport: "http_json",
  method: "POST",
  url,
  json: body,
  headers: { Authorization: `Bearer ${token}` },
  native,
})

const messageBody = (
  tid: string,
  text: string,
  actions: ReadonlyArray<PostAction>,
): { readonly [key: string]: unknown } => {
  const to = decodeThreadId(tid).waId
  if (actions.length > 0) {
    const buttons = actions.slice(0, 3).map((action) => ({
      type: "reply",
      reply: { id: buttonData(action) || buttonText(action), title: buttonText(action) },
    }))
    return {
      messaging_product: "whatsapp",
      to,
      type: "interactive",
      interactive: {
        type: "button",
        body: { text },
        action: { buttons },
      },
    }
  }
  return {
    messaging_product: "whatsapp",
    to,
    type: "text",
    text: { body: text },
  }
}

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpJsonCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  const token = configString(conn.config, "accessToken")
  if (token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "No accessToken in connection config",
        commandTag: command.tag,
      }),
    )
  }
  const phoneId = configString(conn.config, "phoneNumberId")
  const url = `${GRAPH_BASE}/${phoneId}/messages`
  switch (command.tag) {
    case "Post":
      return Effect.succeed(
        req(
          url,
          token,
          messageBody(command.thread_id, command.text, command.actions),
          command.actions.length > 0 ? "interactive" : "text",
        ),
      )
    case "React":
      return Effect.succeed(
        req(
          url,
          token,
          {
            messaging_product: "whatsapp",
            to: decodeThreadId(command.thread_id).waId,
            type: "reaction",
            reaction: { message_id: command.message_id, emoji: command.emoji },
          },
          "reaction",
        ),
      )
    default:
      return Effect.fail(
        new AdapterError({
          reason: `WhatsApp does not support ${command.tag}`,
          commandTag: command.tag,
        }),
      )
  }
}

const spec = {
  name: "whatsapp",
  parse: parseWhatsAppUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const whatsappLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const whatsappHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const verifyWhatsApp = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const secret = configString(conn.config, "appSecret")
  if (secret.length === 0) {
    return true
  }
  const got = firstHeader(headers, "X-Hub-Signature-256")
  const digest = hmacSha256Hex(secret, body)
  return timingSafeEqualUtf8(`sha256=${digest}`, got)
}

export const whatsapp = () => ({
  name: "whatsapp" as const,
  parse: parseWhatsAppUpdate,
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
    "react",
    "receipts",
  ],
  format: (text: string): string => text,
  openModal: undefined as never,
})
