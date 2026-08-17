import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { AdapterPort } from "../core/ports.ts"
import type { PlannedCall } from "./plan.ts"

const skipTags = new Set(["Host", "Subscribe", "SetState"])

export type AdapterPlan = {
  readonly name: string
  readonly parse: AdapterPort["Type"]["parse"]
  readonly overlapKey: (event: Event) => string
  readonly planAck: (event: Event, conn: Connection) => PlannedCall | undefined
  readonly planCommand: (
    command: Command,
    conn: Connection,
  ) => Effect.Effect<PlannedCall | undefined, AdapterError>
}

export const recordingLayer = (
  spec: AdapterPlan,
  sink: PlannedCall[],
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: spec.name,
    parse: spec.parse,
    overlapKey: spec.overlapKey,
    ack: (event, conn) =>
      Effect.sync(() => {
        const call = spec.planAck(event, conn)
        if (call !== undefined) {
          sink.push(call)
        }
        return { ok: true as const }
      }),
    execute: (command, conn) =>
      spec.planCommand(command, conn).pipe(
        Effect.map((call) => {
          if (call !== undefined) {
            sink.push(call)
          }
          return { ok: true as const }
        }),
      ),
  })

export const skippedCommand = (command: Command): boolean =>
  skipTags.has(command.tag)
