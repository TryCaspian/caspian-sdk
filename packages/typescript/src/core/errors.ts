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

export class ProvisionError extends Schema.TaggedError<ProvisionError>()("ProvisionError", {
  reason: Schema.String,
}) {}

export class HostError extends Schema.TaggedError<HostError>()("HostError", {
  reason: Schema.String,
  handlerId: Schema.String,
}) {}

export const CaspianError = Schema.Union(
  DecodeError,
  AdapterError,
  ProvisionError,
  HostError,
)
export type CaspianError = typeof CaspianError.Type
