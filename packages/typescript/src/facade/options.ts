import * as Schema from "effect/Schema"
import { Bound, OverlapPolicy } from "../core/app.ts"
import { ChatKind } from "../core/events.ts"

export const ChannelOption = Schema.Union(
  Schema.String,
  Schema.Array(Schema.String),
)

export const OnMessageOptions = Schema.Struct({
  channel: Schema.optional(ChannelOption),
  kind: Schema.optional(ChatKind),
  overlap: Schema.optional(OverlapPolicy),
  bound: Schema.optional(Bound),
  /** Instant acknowledgement sent before the handler runs. Answers the
   *  silence on channels with no typing indicator (email, SMS, X). */
  ack: Schema.optional(Schema.String),
})
export type OnMessageOptions = typeof OnMessageOptions.Type

export const OnActionOptions = Schema.Struct({
  channel: Schema.optional(ChannelOption),
  overlap: Schema.optional(OverlapPolicy),
  bound: Schema.optional(Bound),
  /** Instant acknowledgement sent before the handler runs. Answers the
   *  silence on channels with no typing indicator (email, SMS, X). */
  ack: Schema.optional(Schema.String),
})
export type OnActionOptions = typeof OnActionOptions.Type
