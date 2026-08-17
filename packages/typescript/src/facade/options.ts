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
})
export type OnMessageOptions = typeof OnMessageOptions.Type

export const OnActionOptions = Schema.Struct({
  channel: Schema.optional(ChannelOption),
  overlap: Schema.optional(OverlapPolicy),
  bound: Schema.optional(Bound),
})
export type OnActionOptions = typeof OnActionOptions.Type
