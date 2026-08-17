import * as Schema from "effect/Schema"
import { ChatKind } from "./events.ts"
import type { Event } from "./events.ts"

export const MatchAll = Schema.Struct({
  op: Schema.Literal("all"),
})
export type MatchAll = typeof MatchAll.Type

export const EventKind = Schema.Literal(
  "message",
  "action",
  "reaction",
  "receipt",
  "member_join",
  "member_leave",
  "edited",
  "deleted",
)
export type EventKind = typeof EventKind.Type

export const MatchKind = Schema.Struct({
  op: Schema.Literal("kind"),
  kind: EventKind,
})
export type MatchKind = typeof MatchKind.Type

export const MatchChannel = Schema.Struct({
  op: Schema.Literal("channel"),
  channels: Schema.Array(Schema.String),
})
export type MatchChannel = typeof MatchChannel.Type

export const MatchChatKind = Schema.Struct({
  op: Schema.Literal("chat_kind"),
  chat_kind: ChatKind,
})
export type MatchChatKind = typeof MatchChatKind.Type

export interface And {
  readonly op: "and"
  readonly left: Predicate
  readonly right: Predicate
}

export interface Or {
  readonly op: "or"
  readonly left: Predicate
  readonly right: Predicate
}

export interface Not {
  readonly op: "not"
  readonly inner: Predicate
}

export type Predicate =
  | MatchAll
  | MatchKind
  | MatchChannel
  | MatchChatKind
  | And
  | Or
  | Not

export const And: Schema.Schema<And> = Schema.Struct({
  op: Schema.Literal("and"),
  left: Schema.suspend((): Schema.Schema<Predicate> => Predicate),
  right: Schema.suspend((): Schema.Schema<Predicate> => Predicate),
})

export const Or: Schema.Schema<Or> = Schema.Struct({
  op: Schema.Literal("or"),
  left: Schema.suspend((): Schema.Schema<Predicate> => Predicate),
  right: Schema.suspend((): Schema.Schema<Predicate> => Predicate),
})

export const Not: Schema.Schema<Not> = Schema.Struct({
  op: Schema.Literal("not"),
  inner: Schema.suspend((): Schema.Schema<Predicate> => Predicate),
})

export const Predicate: Schema.Schema<Predicate> = Schema.Union(
  MatchAll,
  MatchKind,
  MatchChannel,
  MatchChatKind,
  And,
  Or,
  Not,
)

export const message = (): MatchKind => ({ op: "kind", kind: "message" })
export const action = (): MatchKind => ({ op: "kind", kind: "action" })
export const reaction = (): MatchKind => ({ op: "kind", kind: "reaction" })
export const receipt = (): MatchKind => ({ op: "kind", kind: "receipt" })
export const memberJoin = (): MatchKind => ({ op: "kind", kind: "member_join" })
export const memberLeave = (): MatchKind => ({ op: "kind", kind: "member_leave" })
export const edited = (): MatchKind => ({ op: "kind", kind: "edited" })
export const deleted = (): MatchKind => ({ op: "kind", kind: "deleted" })
export const channel = (...names: string[]): MatchChannel => ({
  op: "channel",
  channels: names,
})
export const dm = (): MatchChatKind => ({ op: "chat_kind", chat_kind: "dm" })
export const group = (): MatchChatKind => ({ op: "chat_kind", chat_kind: "group" })

export const evaluate = (
  pred: Predicate,
  event: Event,
  channelName: string,
): boolean => {
  switch (pred.op) {
    case "all":
      return true
    case "kind":
      return event.kind === pred.kind
    case "channel":
      return pred.channels.includes(channelName)
    case "chat_kind":
      return "chat_kind" in event && event.chat_kind === pred.chat_kind
    case "and":
      return (
        evaluate(pred.left, event, channelName) &&
        evaluate(pred.right, event, channelName)
      )
    case "or":
      return (
        evaluate(pred.left, event, channelName) ||
        evaluate(pred.right, event, channelName)
      )
    case "not":
      return !evaluate(pred.inner, event, channelName)
  }
}
