import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import { AdapterPort } from "../core/ports.ts"
import { portFields, sentFromCall, type AdapterPlan } from "./port.ts"
import type { PlannedCall } from "./plan.ts"

const skipTags = new Set(["Host", "Subscribe", "SetState"])

export type { AdapterPlan }

export const recordingLayer = (
  spec: AdapterPlan,
  sink: PlannedCall[],
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    ...portFields(spec),
    acknowledge: (event, conn) =>
      Effect.sync(() => {
        const call = spec.planAck(event, conn)
        if (call !== undefined) {
          sink.push(call)
        }
        return sentFromCall(call)
      }),
    execute: (command, conn) =>
      spec.planCommand(command, conn).pipe(
        Effect.map((call) => {
          if (call !== undefined) {
            sink.push(call)
          }
          return sentFromCall(call)
        }),
      ),
  })

export const skippedCommand = (
  command: { readonly tag: string },
): boolean => skipTags.has(command.tag)
