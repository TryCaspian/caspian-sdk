import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Connection } from "../../core/connection.ts"
import { AdapterError } from "../../core/errors.ts"
import { AdapterPort, emptySent, type Sent } from "../../core/ports.ts"
import {
  asHttpJson,
  markReadSent,
  planAck,
  planCommand,
  planPoll,
  telegramCommandError,
  telegramSent,
  tokenOf,
  type TelegramCall,
} from "./execute.ts"
import { overlapKey } from "./ids.ts"
import {
  formatTelegram,
  telegramCapabilities,
  verifyTelegram,
} from "./layer.ts"
import { parseTelegramUpdate } from "./parse.ts"

export type TelegramFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>

const postCall = (
  fetchImpl: TelegramFetch,
  conn: Connection,
  call: TelegramCall,
): Effect.Effect<Sent, AdapterError> => {
  const token = tokenOf(conn)
  if (token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "missing botToken on connection",
        commandTag: call.method,
      }),
    )
  }
  const planned = asHttpJson(call, token)
  return Effect.tryPromise({
    try: async () => {
      const response = await fetchImpl(planned.url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(call.body),
      })
      if (!response.ok) {
        throw new Error(`telegram ${call.method} HTTP ${response.status}`)
      }
      let messageId = ""
      try {
        const payload: unknown = await response.json()
        if (
          typeof payload === "object" &&
          payload !== null &&
          "result" in payload &&
          typeof (payload as { result?: { message_id?: unknown } }).result ===
            "object"
        ) {
          const result = (payload as { result: { message_id?: unknown } }).result
          if (result.message_id !== undefined) {
            messageId = String(result.message_id)
          }
        }
      } catch {
        messageId = ""
      }
      return { ...telegramSent(call, token), message_id: messageId }
    },
    catch: (cause) =>
      new AdapterError({
        reason: cause instanceof Error ? cause.message : String(cause),
        commandTag: call.method,
      }),
  })
}

export const telegramHttpLayer = (
  fetchImpl: TelegramFetch = fetch,
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: "telegram",
    parse: (raw) => Effect.succeed(parseTelegramUpdate(raw)),
    overlapKey,
    verify: verifyTelegram,
    acknowledge: (event, conn) => {
      const call = planAck(event)
      if (call === undefined) {
        return Effect.succeed(emptySent())
      }
      return postCall(fetchImpl, conn, call)
    },
    execute: (command, conn) => {
      const error = telegramCommandError(command)
      if (error !== undefined) {
        return Effect.fail(error)
      }
      if (command.tag === "MarkRead") {
        return Effect.succeed(markReadSent())
      }
      const call = planCommand(command)
      if (call === undefined) {
        return Effect.succeed(emptySent())
      }
      return postCall(fetchImpl, conn, call)
    },
    capabilities: telegramCapabilities,
    format: formatTelegram,
    poll: (offset, conn) => {
      const token = tokenOf(conn)
      if (token.length === 0) {
        return Effect.fail(
          new AdapterError({
            reason: "No botToken in connection config",
            commandTag: "getUpdates",
          }),
        )
      }
      return Effect.succeed(telegramSent(planPoll(offset), token))
    },
  })
