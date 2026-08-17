import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import { AdapterPort } from "../../core/ports.ts"
import { planAck, planCommand, type TelegramCall } from "./execute.ts"
import { overlapKey } from "./ids.ts"
import { parseTelegramUpdate } from "./parse.ts"

export const telegramLayer = (
  sink: TelegramCall[],
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: "telegram",
    parse: (raw) => Effect.succeed(parseTelegramUpdate(raw)),
    overlapKey,
    ack: (event) =>
      Effect.sync(() => {
        const call = planAck(event)
        if (call !== undefined) {
          sink.push(call)
        }
        return { ok: true as const }
      }),
    execute: (command) =>
      Effect.sync(() => {
        const call = planCommand(command)
        if (call !== undefined) {
          sink.push(call)
        }
        return { ok: true as const }
      }),
  })
