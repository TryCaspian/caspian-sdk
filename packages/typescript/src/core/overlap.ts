/**
 * Overlap FSM — pure transitions, no queues, no I/O.
 *
 * The rule names the policy. This module only computes the next state.
 * The runner owns the actual wait/buffer.
 */
import * as Schema from "effect/Schema"
import type { OverlapPolicy } from "./app.ts"

export const SlotStatus = Schema.Literal("idle", "busy")
export type SlotStatus = typeof SlotStatus.Type

export const OverlapState = Schema.Struct({
  status: SlotStatus,
  queued: Schema.Number.pipe(Schema.int(), Schema.greaterThanOrEqualTo(0)),
  skipped_count: Schema.Number.pipe(Schema.int(), Schema.greaterThanOrEqualTo(0)),
})
export type OverlapState = typeof OverlapState.Type

export const OverlapDecision = Schema.Literal("execute", "enqueue", "drop")
export type OverlapDecision = typeof OverlapDecision.Type

export const OverlapResult = Schema.Struct({
  decision: OverlapDecision,
  new_state: OverlapState,
  skipped_count: Schema.Number.pipe(Schema.int(), Schema.greaterThanOrEqualTo(0)),
})
export type OverlapResult = typeof OverlapResult.Type

export const idleOverlapState: OverlapState = {
  status: "idle",
  queued: 0,
  skipped_count: 0,
}

const busy = (queued: number, skipped_count: number): OverlapState => ({
  status: "busy",
  queued,
  skipped_count,
})

const result = (
  decision: OverlapDecision,
  new_state: OverlapState,
  skipped_count = 0,
): OverlapResult => ({ decision, new_state, skipped_count })

export const overlapTransition = (
  state: OverlapState,
  policy: OverlapPolicy,
  bound: number,
): OverlapResult => {
  switch (policy) {
    case "parallel":
      return result("execute", state)
    case "drop":
      if (state.status === "busy") {
        return result("drop", state)
      }
      return result("execute", busy(0, 0))
    case "queue":
      if (state.status === "idle") {
        return result("execute", busy(0, 0))
      }
      if (state.queued >= bound) {
        return result("drop", state)
      }
      return result(
        "enqueue",
        busy(state.queued + 1, state.skipped_count + 1),
      )
    case "debounce":
      if (state.status === "idle") {
        return result("execute", busy(0, 0))
      }
      return result("enqueue", busy(1, state.skipped_count + 1))
  }
}

export const drainTransition = (
  state: OverlapState,
  policy: OverlapPolicy,
): OverlapResult => {
  if (state.queued > 0 && (policy === "queue" || policy === "debounce")) {
    return result("execute", busy(0, 0), state.skipped_count)
  }
  return result("drop", idleOverlapState)
}
