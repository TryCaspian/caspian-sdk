import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Connection } from "../../core/connection.ts"
import { AdapterError } from "../../core/errors.ts"
import { AdapterPort } from "../../core/ports.ts"
import { inboundHeaders } from "../port.ts"
import { configString, firstHeader, timingSafeEqualUtf8 } from "../util.ts"
import {
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
import { parseTelegramUpdate } from "./parse.ts"

const MDV2_SPECIAL = "_*[]()~`>#+-=|{}.!"

export const formatTelegram = (text: string): string => {
  let out = ""
  for (const ch of text) {
    out += MDV2_SPECIAL.includes(ch) ? `\\${ch}` : ch
  }
  return out
}

export const verifyTelegram = (raw: unknown, conn: Connection): boolean => {
  const expected =
    configString(conn.config, "webhookSecret") ||
    configString(conn.config, "secretToken")
  if (expected.length === 0) {
    return true
  }
  const got = firstHeader(inboundHeaders(raw), "X-Telegram-Bot-Api-Secret-Token")
  return timingSafeEqualUtf8(expected, got)
}

<<<<<<< HEAD
export const telegramCapabilities = (): ReadonlyArray<string> => [
  "receive",
  "reply",
  "send",
  "media",
  "buttons",
  "edit",
  "delete",
  "react",
  "typing",
  "pin",
  "forward",
  "threading",
  "membership",
]
=======
import { capabilitiesOf } from "../../catalog.ts"

export const telegramCapabilities = (): ReadonlyArray<string> =>
  capabilitiesOf("telegram")
>>>>>>> e972615f6ea1c870bc2e3da11bdd29c3d9465ef6

export const telegramLayer = (
  sink: TelegramCall[],
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: "telegram",
    parse: (raw) => Effect.succeed(parseTelegramUpdate(raw)),
    overlapKey,
    verify: verifyTelegram,
    acknowledge: (event, conn) =>
      Effect.sync(() => {
        const call = planAck(event)
        if (call !== undefined) {
          sink.push(call)
        }
        return telegramSent(call, tokenOf(conn))
      }),
    execute: (command, conn) => {
      const error = telegramCommandError(command)
      if (error !== undefined) {
        return Effect.fail(error)
      }
      if (command.tag === "MarkRead") {
        return Effect.succeed(markReadSent())
      }
      return Effect.sync(() => {
        const call = planCommand(command)
        if (call !== undefined) {
          sink.push(call)
        }
        return telegramSent(call, tokenOf(conn))
      })
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
      return Effect.sync(() => {
        const call = planPoll(offset)
        sink.push(call)
        return telegramSent(call, token)
      })
    },
  })
