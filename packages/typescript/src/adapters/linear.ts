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
  encodePrefixed,
  firstHeader,
  hmacSha256Hex,
  isRecord,
  jsonObjectOf,
  suffixAfter,
  timingSafeEqualUtf8,
} from "./util.ts"

const PREFIX = "linear:"
const GRAPHQL_URL = "https://api.linear.app/graphql"
const COMMENT_MUTATION =
  "mutation($input: CommentCreateInput!){commentCreate(input:$input){success}}"

export type NativeThread = { readonly issueId: string }

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.issueId)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  issueId: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  String(event.thread_id)

const parseComment = (data: Record<string, unknown>): ReadonlyArray<Event> => {
  const issue = isRecord(data.issue) ? data.issue : {}
  const user = isRecord(data.user) ? data.user : {}
  const issueId = issue.id !== undefined ? String(issue.id) : ""
  return [
    {
      kind: "message",
      thread_id: encodeThreadId({ issueId }),
      text: typeof data.body === "string" ? data.body : "",
      chat_kind: "channel",
      sender: user.id !== undefined ? String(user.id) : "",
      raw: asJsonObject(data),
    },
  ]
}

const parseIssue = (data: Record<string, unknown>): ReadonlyArray<Event> => {
  const issueId = data.id !== undefined ? String(data.id) : ""
  const text =
    typeof data.title === "string"
      ? data.title
      : typeof data.description === "string"
        ? data.description
        : ""
  return [
    {
      kind: "message",
      thread_id: encodeThreadId({ issueId }),
      text,
      chat_kind: "channel",
      sender: data.creatorId !== undefined ? String(data.creatorId) : "",
      raw: asJsonObject(data),
    },
  ]
}

export const parseLinearUpdate = (
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
  const data = isRecord(payload.data) ? payload.data : {}
  if (payload.type === "Comment") {
    return Effect.succeed(parseComment(data))
  }
  if (payload.type === "Issue") {
    return Effect.succeed(parseIssue(data))
  }
  return Effect.succeed([])
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
        reason: `Unsupported command: ${command.tag}`,
        commandTag: command.tag,
      }),
    )
  }
  return Effect.succeed({
    transport: "http_json",
    method: "POST",
    url: GRAPHQL_URL,
    json: {
      query: COMMENT_MUTATION,
      variables: {
        input: {
          issueId: decodeThreadId(command.thread_id).issueId,
          body: command.text,
        },
      },
    },
    headers: { Authorization: apiKey },
    native: "commentCreate",
  })
}

const spec = {
  name: "linear",
  parse: parseLinearUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const linearLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)
export const linearHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const verifyLinear = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const secret = configString(conn.config, "webhookSecret")
  if (secret.length === 0) {
    return true
  }
  const got = firstHeader(headers, "Linear-Signature")
  return timingSafeEqualUtf8(hmacSha256Hex(secret, body), got)
}

export const linear = () => ({
  name: "linear" as const,
  parse: parseLinearUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => [
    "receive",
    "reply",
    "send",
    "threading",
  ],
  format: (text: string): string => text,
  openModal: undefined as never,
})
