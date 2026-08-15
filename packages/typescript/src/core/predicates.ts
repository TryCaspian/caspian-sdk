import * as Schema from "effect/Schema"
import { ChatKind } from "./events.ts"

export const MatchAll = Schema.Struct({
  op: Schema.Literal("all"),
})
export type MatchAll = typeof MatchAll.Type

export const MatchKind = Schema.Struct({
  op: Schema.Literal("kind"),
  kind: Schema.Literal("message", "action", "reaction"),
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
export const channel = (...names: string[]): MatchChannel => ({
  op: "channel",
  channels: names,
})
export const dm = (): MatchChatKind => ({ op: "chat_kind", chat_kind: "dm" })
export const group = (): MatchChatKind => ({ op: "chat_kind", chat_kind: "group" })
