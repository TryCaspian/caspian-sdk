import * as Schema from "effect/Schema"
import { ThreadId } from "./ids.ts"
import { JsonObject } from "./json.ts"

export const ChatKind = Schema.Literal("dm", "group", "channel")
export type ChatKind = typeof ChatKind.Type

export const Message = Schema.Struct({
  kind: Schema.Literal("message"),
  thread_id: ThreadId,
  text: Schema.String,
  chat_kind: ChatKind,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Message = typeof Message.Type

export const Action = Schema.Struct({
  kind: Schema.Literal("action"),
  thread_id: ThreadId,
  data: Schema.String,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Action = typeof Action.Type

export const Reaction = Schema.Struct({
  kind: Schema.Literal("reaction"),
  thread_id: ThreadId,
  emoji: Schema.String,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Reaction = typeof Reaction.Type

export const Event = Schema.Union(Message, Action, Reaction)
export type Event = typeof Event.Type
