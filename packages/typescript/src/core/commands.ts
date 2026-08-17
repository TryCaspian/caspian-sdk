import * as Schema from "effect/Schema"
import { Attachment, Block } from "./events.ts"
import { ThreadId } from "./ids.ts"
import { Json, JsonObject } from "./json.ts"

export const ButtonStyle = Schema.Literal("default", "primary", "danger")
export type ButtonStyle = typeof ButtonStyle.Type

export const PostAction = Schema.Struct({
  label: Schema.optional(Schema.String),
  text: Schema.optional(Schema.String),
  data: Schema.optional(Schema.String),
  value: Schema.optional(Schema.String),
  url: Schema.optional(Schema.String),
  style: Schema.optional(ButtonStyle),
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

export const Reply = Schema.Struct({
  tag: Schema.Literal("Reply"),
  thread_id: ThreadId,
  reply_to: Schema.String,
  text: Schema.String,
  actions: Schema.optionalWith(Schema.Array(PostAction), {
    default: () => [],
  }),
})
export type Reply = typeof Reply.Type

export const SendBlocks = Schema.Struct({
  tag: Schema.Literal("SendBlocks"),
  thread_id: ThreadId,
  blocks: Schema.Array(Block),
  text: Schema.optionalWith(Schema.String, { default: () => "" }),
  actions: Schema.optionalWith(Schema.Array(PostAction), {
    default: () => [],
  }),
})
export type SendBlocks = typeof SendBlocks.Type

export const SendMedia = Schema.Struct({
  tag: Schema.Literal("SendMedia"),
  thread_id: ThreadId,
  attachment: Attachment,
  caption: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type SendMedia = typeof SendMedia.Type

export const Edit = Schema.Struct({
  tag: Schema.Literal("Edit"),
  thread_id: ThreadId,
  message_id: Schema.String,
  text: Schema.String,
  actions: Schema.optionalWith(Schema.Array(PostAction), {
    default: () => [],
  }),
})
export type Edit = typeof Edit.Type

export const Delete = Schema.Struct({
  tag: Schema.Literal("Delete"),
  thread_id: ThreadId,
  message_id: Schema.String,
})
export type Delete = typeof Delete.Type

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

export const Pin = Schema.Struct({
  tag: Schema.Literal("Pin"),
  thread_id: ThreadId,
  message_id: Schema.String,
})
export type Pin = typeof Pin.Type

export const Unpin = Schema.Struct({
  tag: Schema.Literal("Unpin"),
  thread_id: ThreadId,
  message_id: Schema.String,
})
export type Unpin = typeof Unpin.Type

export const Forward = Schema.Struct({
  tag: Schema.Literal("Forward"),
  from_thread_id: ThreadId,
  to_thread_id: ThreadId,
  message_id: Schema.String,
})
export type Forward = typeof Forward.Type

export const MarkRead = Schema.Struct({
  tag: Schema.Literal("MarkRead"),
  thread_id: ThreadId,
  message_id: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type MarkRead = typeof MarkRead.Type

export const Initiate = Schema.Struct({
  tag: Schema.Literal("Initiate"),
  thread_id: ThreadId,
  text: Schema.String,
  actions: Schema.optionalWith(Schema.Array(PostAction), {
    default: () => [],
  }),
})
export type Initiate = typeof Initiate.Type

export const ScheduleSend = Schema.Struct({
  tag: Schema.Literal("ScheduleSend"),
  thread_id: ThreadId,
  text: Schema.String,
  send_at: Schema.Number,
  actions: Schema.optionalWith(Schema.Array(PostAction), {
    default: () => [],
  }),
})
export type ScheduleSend = typeof ScheduleSend.Type

export const OpenModal = Schema.Struct({
  tag: Schema.Literal("OpenModal"),
  thread_id: ThreadId,
  trigger_id: Schema.String,
  blocks: Schema.Array(Block),
  title: Schema.optionalWith(Schema.String, { default: () => "" }),
  callback_id: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type OpenModal = typeof OpenModal.Type

export const UpdateModal = Schema.Struct({
  tag: Schema.Literal("UpdateModal"),
  view_id: Schema.String,
  blocks: Schema.Array(Block),
  title: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type UpdateModal = typeof UpdateModal.Type

export const ListHistory = Schema.Struct({
  tag: Schema.Literal("ListHistory"),
  thread_id: ThreadId,
  limit: Schema.optionalWith(Schema.Number, { default: () => 20 }),
  before: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type ListHistory = typeof ListHistory.Type

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
  Reply,
  SendBlocks,
  SendMedia,
  Edit,
  Delete,
  React,
  Typing,
  Pin,
  Unpin,
  Forward,
  MarkRead,
  Initiate,
  ScheduleSend,
  OpenModal,
  UpdateModal,
  ListHistory,
  Subscribe,
  SetState,
  Call,
  Host,
)
export type Command = typeof Command.Type
