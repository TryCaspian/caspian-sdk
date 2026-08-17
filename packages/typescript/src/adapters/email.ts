import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError, DecodeError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { ThreadId } from "../core/ids.ts"
import { httpLayer, type AdapterFetch } from "./http.ts"
import type { PlannedCall, SmtpCall } from "./plan.ts"
import { recordingLayer, skippedCommand } from "./recording.ts"
import {
  asJsonObject,
  configString,
  encodePrefixed,
  isRecord,
  jsonObjectOf,
  suffixAfter,
} from "./util.ts"

const PREFIX = "email:"

export type NativeThread = { readonly address: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.address.toLowerCase())

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  address: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

const parseAddr = (raw: string): string => {
  const match = /<([^>]+)>/.exec(raw)
  const addr = (match?.[1] ?? raw).trim()
  return addr.toLowerCase()
}

const firstOf = (value: unknown): string => {
  if (Array.isArray(value) && value.length > 0) {
    return String(value[0])
  }
  return value ? String(value) : ""
}

const mimeHeadersAndBody = (
  content: string,
): { readonly headers: Record<string, string>; readonly body: string } => {
  const normalized = content.replaceAll("\r\n", "\n")
  const split = normalized.indexOf("\n\n")
  const headerText = split < 0 ? normalized : normalized.slice(0, split)
  const body = split < 0 ? "" : normalized.slice(split + 2)
  const headers: Record<string, string> = {}
  for (const line of headerText.split("\n")) {
    const cut = line.indexOf(":")
    if (cut < 0) {
      continue
    }
    headers[line.slice(0, cut).toLowerCase()] = line.slice(cut + 1).trim()
  }
  return { headers, body }
}

type EmailFields = {
  readonly sender: string
  readonly body: string
  readonly messageId: string
}

const fromSimple = (data: Record<string, unknown>): EmailFields | undefined => {
  const keys = ["from", "to", "subject", "body", "message_id"]
  if (!keys.some((key) => key in data)) {
    return undefined
  }
  return {
    sender: parseAddr(String(data.from ?? "")),
    body: String(data.body ?? ""),
    messageId: String(data.message_id ?? ""),
  }
}

const fromSns = (data: Record<string, unknown>): EmailFields | undefined => {
  const innerRaw = data.Message
  if (typeof innerRaw !== "string") {
    return undefined
  }
  const innerUnknown: unknown = JSON.parse(innerRaw)
  if (!isRecord(innerUnknown)) {
    return undefined
  }
  const mail = isRecord(innerUnknown.mail) ? innerUnknown.mail : {}
  const headers = isRecord(mail.commonHeaders) ? mail.commonHeaders : {}
  const content = typeof innerUnknown.content === "string" ? innerUnknown.content : ""
  const mime = content.length > 0 ? mimeHeadersAndBody(content) : { headers: {}, body: "" }
  const fromRaw =
    firstOf(headers.from) || String(mail.source ?? "") || mime.headers.from || ""
  const messageId =
    String(headers.messageId ?? "") ||
    String(mail.messageId ?? "") ||
    mime.headers["message-id"] ||
    ""
  return {
    sender: parseAddr(fromRaw),
    body: mime.body,
    messageId,
  }
}

export const parseEmailUpdate = (
  raw: unknown,
): Effect.Effect<ReadonlyArray<Event>, DecodeError> => {
  const decoded = jsonObjectOf(raw)
  if (!decoded.ok) {
    return Effect.fail(decoded.error)
  }
  const data = decoded.value
  if (data === undefined) {
    return Effect.succeed([])
  }
  let fields: EmailFields | undefined
  try {
    if (data.Type === "Notification" && "Message" in data) {
      fields = fromSns(data)
    } else {
      fields = fromSimple(data)
    }
  } catch (cause) {
    return Effect.fail(
      new DecodeError({
        reason: `Invalid email payload: ${cause instanceof Error ? cause.message : String(cause)}`,
      }),
    )
  }
  if (fields === undefined) {
    return Effect.succeed([])
  }
  return Effect.succeed([
    {
      kind: "message",
      thread_id: encodeThreadId({ address: fields.sender }),
      text: fields.body,
      chat_kind: "dm",
      sender: fields.sender,
      raw: asJsonObject(data),
    },
  ])
}

const emailReq = (
  conn: Connection,
  to: string,
  body: string,
): SmtpCall => ({
  transport: "smtp",
  native: "sendmail",
  email: {
    from: configString(conn.config, "fromAddress"),
    to,
    subject: configString(conn.config, "defaultSubject") || "(no subject)",
    body,
    in_reply_to: "",
    references: "",
    attachments: [],
  },
})

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<SmtpCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  if (command.tag === "Post") {
    return Effect.succeed(
      emailReq(conn, decodeThreadId(command.thread_id).address, command.text),
    )
  }
  return Effect.fail(
    new AdapterError({
      reason: `Email adapter does not support command: ${command.tag}`,
      commandTag: command.tag,
    }),
  )
}

const spec = {
  name: "email",
  parse: parseEmailUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const emailLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const emailHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const email = () => ({
  name: "email" as const,
  parse: parseEmailUpdate,
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
    "threading",
  ],
  format: (text: string): string => text,
  openModal: undefined as never,
})
