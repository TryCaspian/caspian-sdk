import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Connection } from "../../core/connection.ts"
import { AdapterError } from "../../core/errors.ts"
import { AdapterPort } from "../../core/ports.ts"
import { planAck, planCommand, type TelegramCall } from "./execute.ts"
import { overlapKey } from "./ids.ts"
import { parseTelegramUpdate } from "./parse.ts"

const tokenOf = (conn: Connection): string => {
  const token = conn.config.botToken
  return typeof token === "string" ? token : ""
}

export type TelegramFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>

const postCall = (
  fetchImpl: TelegramFetch,
  conn: Connection,
  call: TelegramCall,
): Effect.Effect<{ readonly ok: true }, AdapterError> => {
  const token = tokenOf(conn)
  if (token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "missing botToken on connection",
        commandTag: call.method,
      }),
    )
  }
  return Effect.tryPromise({
    try: async () => {
      const response = await fetchImpl(
        `https://api.telegram.org/bot${token}/${call.method}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(call.body),
        },
      )
      if (!response.ok) {
        throw new Error(`telegram ${call.method} HTTP ${response.status}`)
      }
      return { ok: true as const }
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
    ack: (event, conn) => {
      const call = planAck(event)
      if (call === undefined) {
        return Effect.succeed({ ok: true as const })
      }
      return postCall(fetchImpl, conn, call)
    },
    execute: (command, conn) => {
      const call = planCommand(command)
      if (call === undefined) {
        return Effect.succeed({ ok: true as const })
      }
      return postCall(fetchImpl, conn, call)
    },
  })
