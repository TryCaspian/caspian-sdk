/**
 * Closed error ADT. No throw across the core boundary.
 */
import * as Schema from "effect/Schema"

export class DecodeError extends Schema.TaggedError<DecodeError>()("DecodeError", {
  reason: Schema.String,
}) {}

export class AdapterError extends Schema.TaggedError<AdapterError>()("AdapterError", {
  reason: Schema.String,
  commandTag: Schema.optionalWith(Schema.String, { default: () => "" }),
}) {}

export class OverlapFull extends Schema.TaggedError<OverlapFull>()("OverlapFull", {
  threadId: Schema.String,
  bound: Schema.Number,
}) {}

export class ProvisionError extends Schema.TaggedError<ProvisionError>()("ProvisionError", {
  reason: Schema.String,
}) {}

export const CaspianError = Schema.Union(
  DecodeError,
  AdapterError,
  OverlapFull,
  ProvisionError,
)
export type CaspianError = typeof CaspianError.Type
