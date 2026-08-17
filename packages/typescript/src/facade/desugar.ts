import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import type { Overlap, Rule } from "../core/app.ts"
import type { DecodeError } from "../core/errors.ts"
import { decodeStrict } from "../core/parse.ts"
import type { Predicate } from "../core/predicates.ts"
import {
  OnActionOptions,
  OnMessageOptions,
} from "./options.ts"

export const decodeOnMessageOptions = decodeStrict(OnMessageOptions)
export const decodeOnActionOptions = decodeStrict(OnActionOptions)

const unwrap = <A>(effect: Effect.Effect<A, DecodeError>): A => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isLeft(result)) {
    throw result.left
  }
  return result.right
}

const channelsOf = (
  channel: string | ReadonlyArray<string> | undefined,
): ReadonlyArray<string> | undefined => {
  if (channel === undefined) {
    return undefined
  }
  return typeof channel === "string" ? [channel] : channel
}

const andAll = (parts: ReadonlyArray<Predicate>): Predicate => {
  const first = parts[0]
  if (first === undefined) {
    return { op: "all" }
  }
  return parts.slice(1).reduce<Predicate>(
    (left, right) => ({ op: "and", left, right }),
    first,
  )
}

const overlapOf = (
  policy: Overlap["policy"],
  bound: number | undefined,
): Overlap => ({
  policy,
  bound: bound ?? 16,
})

export const desugarOnMessage = (
  options: unknown,
  handlerId: string,
): Rule => {
  const value = unwrap(decodeOnMessageOptions(options ?? {}))
  const parts: Predicate[] = [{ op: "kind", kind: "message" }]
  const channels = channelsOf(value.channel)
  if (channels !== undefined) {
    parts.push({ op: "channel", channels: [...channels] })
  }
  if (value.kind !== undefined) {
    parts.push({ op: "chat_kind", chat_kind: value.kind })
  }
  return {
    predicate: andAll(parts),
    overlap: overlapOf(value.overlap ?? "queue", value.bound),
    handler_id: handlerId,
  }
}

export const desugarOnAction = (
  options: unknown,
  handlerId: string,
): Rule => {
  const value = unwrap(decodeOnActionOptions(options ?? {}))
  const parts: Predicate[] = [{ op: "kind", kind: "action" }]
  const channels = channelsOf(value.channel)
  if (channels !== undefined) {
    parts.push({ op: "channel", channels: [...channels] })
  }
  return {
    predicate: andAll(parts),
    overlap: overlapOf(value.overlap ?? "drop", value.bound),
    handler_id: handlerId,
  }
}
