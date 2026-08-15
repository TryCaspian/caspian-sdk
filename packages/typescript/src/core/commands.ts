import * as Schema from "effect/Schema"
import { ThreadId } from "./ids.ts"
import { Json, JsonObject } from "./json.ts"

export const PostAction = Schema.Struct({
  label: Schema.optional(Schema.String),
  text: Schema.optional(Schema.String),
  data: Schema.optional(Schema.String),
  value: Schema.optional(Schema.String),
})
export type PostAction = typeof PostAction.Type

export const Post = Schema.Struct({
  tag: Schema.Literal("Post"),
  thread_id: ThreadId,
  text: Schema.String,
  actions: Schema.optionalWith(Schema.Array(PostAction), {
    default: () => [],
  }),
})
export type Post = typeof Post.Type

export const Edit = Schema.Struct({
  tag: Schema.Literal("Edit"),
  thread_id: ThreadId,
  message_id: Schema.String,
  text: Schema.String,
})
export type Edit = typeof Edit.Type

export const React = Schema.Struct({
  tag: Schema.Literal("React"),
  thread_id: ThreadId,
  message_id: Schema.String,
  emoji: Schema.String,
})
export type React = typeof React.Type

export const Typing = Schema.Struct({
  tag: Schema.Literal("Typing"),
  thread_id: ThreadId,
})
export type Typing = typeof Typing.Type

export const Subscribe = Schema.Struct({
  tag: Schema.Literal("Subscribe"),
  thread_id: ThreadId,
})
export type Subscribe = typeof Subscribe.Type

export const SetState = Schema.Struct({
  tag: Schema.Literal("SetState"),
  thread_id: ThreadId,
  key: Schema.String,
  value: Json,
})
export type SetState = typeof SetState.Type

export const Call = Schema.Struct({
  tag: Schema.Literal("Call"),
  method: Schema.String,
  args: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Call = typeof Call.Type

export const Host = Schema.Struct({
  tag: Schema.Literal("Host"),
  handler_id: Schema.String,
})
export type Host = typeof Host.Type

export const Command = Schema.Union(
  Post,
  Edit,
  React,
  Typing,
  Subscribe,
  SetState,
  Call,
  Host,
)
export type Command = typeof Command.Type
