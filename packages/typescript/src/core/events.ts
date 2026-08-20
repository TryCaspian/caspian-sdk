import * as Schema from "effect/Schema"
import { ThreadId } from "./ids.ts"
import { JsonObject } from "./json.ts"

export const ChatKind = Schema.Literal("dm", "group", "channel")
export type ChatKind = typeof ChatKind.Type

export const AttachmentType = Schema.Literal(
  "photo",
  "file",
  "audio",
  "video",
  "sticker",
  "voice",
)
export type AttachmentType = typeof AttachmentType.Type

export const Attachment = Schema.Struct({
  type: AttachmentType,
  url: Schema.optionalWith(Schema.String, { default: () => "" }),
  file_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  filename: Schema.optionalWith(Schema.String, { default: () => "" }),
  mime_type: Schema.optionalWith(Schema.String, { default: () => "" }),
  size_bytes: Schema.optionalWith(Schema.Number, { default: () => 0 }),
  caption: Schema.optionalWith(Schema.String, { default: () => "" }),
})
export type Attachment = typeof Attachment.Type

export const Block = Schema.Struct({
  type: Schema.String,
  content: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Block = typeof Block.Type

export const ReceiptStatus = Schema.Literal("read", "delivered")
export type ReceiptStatus = typeof ReceiptStatus.Type

export const Message = Schema.Struct({
  kind: Schema.Literal("message"),
  thread_id: ThreadId,
  text: Schema.String,
  chat_kind: ChatKind,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  message_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  attachments: Schema.optionalWith(Schema.Array(Attachment), {
    default: () => [],
  }),
  blocks: Schema.optionalWith(Schema.Array(Block), { default: () => [] }),
  reply_to: Schema.optionalWith(Schema.String, { default: () => "" }),
  topic_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  metadata: Schema.optionalWith(JsonObject, { default: () => ({}) }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Message = typeof Message.Type

export const Action = Schema.Struct({
  kind: Schema.Literal("action"),
  thread_id: ThreadId,
  data: Schema.String,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  message_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  interaction_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  metadata: Schema.optionalWith(JsonObject, { default: () => ({}) }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Action = typeof Action.Type

export const Reaction = Schema.Struct({
  kind: Schema.Literal("reaction"),
  thread_id: ThreadId,
  emoji: Schema.String,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  message_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  removed: Schema.optionalWith(Schema.Boolean, { default: () => false }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Reaction = typeof Reaction.Type

export const Receipt = Schema.Struct({
  kind: Schema.Literal("receipt"),
  thread_id: ThreadId,
  status: ReceiptStatus,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  message_id: Schema.optionalWith(Schema.String, { default: () => "" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Receipt = typeof Receipt.Type

export const MemberJoin = Schema.Struct({
  kind: Schema.Literal("member_join"),
  thread_id: ThreadId,
  member: Schema.String,
  chat_kind: Schema.optionalWith(ChatKind, { default: () => "group" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type MemberJoin = typeof MemberJoin.Type

export const MemberLeave = Schema.Struct({
  kind: Schema.Literal("member_leave"),
  thread_id: ThreadId,
  member: Schema.String,
  chat_kind: Schema.optionalWith(ChatKind, { default: () => "group" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type MemberLeave = typeof MemberLeave.Type

export const Edited = Schema.Struct({
  kind: Schema.Literal("edited"),
  thread_id: ThreadId,
  message_id: Schema.String,
  text: Schema.String,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Edited = typeof Edited.Type

export const Deleted = Schema.Struct({
  kind: Schema.Literal("deleted"),
  thread_id: ThreadId,
  message_id: Schema.String,
  sender: Schema.optionalWith(Schema.String, { default: () => "" }),
  raw: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Deleted = typeof Deleted.Type

export const Event = Schema.Union(
  Message,
  Action,
  Reaction,
  Receipt,
  MemberJoin,
  MemberLeave,
  Edited,
  Deleted,
)
export type Event = typeof Event.Type
