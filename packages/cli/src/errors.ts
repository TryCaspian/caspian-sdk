/**
 * Closed CLI error ADT. Failure is data — never a throw across parse/plan/run.
 */
import * as Schema from "effect/Schema"

export class UsageError extends Schema.TaggedError<UsageError>()("UsageError", {
  reason: Schema.String,
}) {}
