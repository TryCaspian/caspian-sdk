import * as Schema from "effect/Schema"
import { Predicate } from "./predicates.ts"

export const OverlapPolicy = Schema.Literal("queue", "debounce", "drop", "parallel")
export type OverlapPolicy = typeof OverlapPolicy.Type

export const Bound = Schema.Number.pipe(Schema.int(), Schema.greaterThanOrEqualTo(1))
export type Bound = typeof Bound.Type

export const Overlap = Schema.Struct({
  policy: OverlapPolicy,
  bound: Bound,
})
export type Overlap = typeof Overlap.Type

export const Rule = Schema.Struct({
  predicate: Predicate,
  overlap: Overlap,
  handler_id: Schema.String,
  /**
   * Sent the moment a matching event arrives, before the handler runs. The
   * point is channels with no typing indicator (email, SMS, X): without it a
   * human waits on silence while the agent thinks. Empty means no ack.
   */
  ack: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type Rule = typeof Rule.Type

export const App = Schema.Struct({
  rules: Schema.Array(Rule),
})
export type App = typeof App.Type
