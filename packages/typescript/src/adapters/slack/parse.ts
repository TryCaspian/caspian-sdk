import * as Effect from "effect/Effect"
import type { ChatKind, Event } from "../../core/events.ts"
import { DecodeError } from "../../core/errors.ts"
import {
  actionDefaults,
  asJsonObject,
  isRecord,
  jsonObjectOf,
  messageDefaults,
  reactionDefaults,
} from "../util.ts"
import { encodeThreadId } from "./ids.ts"

const chatKindOf = (event: Record<string, unknown>): ChatKind => {
  const ct = event.channel_type
  if (ct === "im") {
    return "dm"
  }
  if (ct === "mpim" || ct === "group") {
    return "group"
  }
  return "channel"
}

const parseMessage = (event: Record<string, unknown>): ReadonlyArray<Event> => {
  if (event.bot_id || event.subtype) {
    return []
  }
  const channel = event.channel !== undefined ? String(event.channel) : ""
  const threadTs = typeof event.thread_ts === "string" ? event.thread_ts : ""
  return [
    {
      kind: "message",
      ...messageDefaults,
      thread_id: encodeThreadId({ channel, threadTs }),
      text: typeof event.text === "string" ? event.text : "",
      chat_kind: chatKindOf(event),
      sender: event.user !== undefined ? String(event.user) : "",
      raw: asJsonObject(event),
    },
  ]
}

const parseReaction = (event: Record<string, unknown>): ReadonlyArray<Event> => {
  const item = isRecord(event.item) ? event.item : {}
  const channel = item.channel !== undefined ? String(item.channel) : ""
  return [
    {
      kind: "reaction",
      ...reactionDefaults,
      thread_id: encodeThreadId({ channel }),
      emoji: typeof event.reaction === "string" ? event.reaction : "",
      sender: event.user !== undefined ? String(event.user) : "",
      raw: asJsonObject(event),
    },
  ]
}

const parseEvent = (event: unknown): ReadonlyArray<Event> => {
  if (!isRecord(event)) {
    return []
  }
  const etype = event.type
  if (etype === "message" || etype === "app_mention") {
    return parseMessage(event)
  }
  if (etype === "reaction_added" || etype === "reaction_removed") {
    return parseReaction(event)
  }
  return []
}

const parseBlockActions = (
  payload: Record<string, unknown>,
): ReadonlyArray<Event> => {
  const actions = Array.isArray(payload.actions) ? payload.actions : []
  const action = isRecord(actions[0]) ? actions[0] : undefined
  if (action === undefined) {
    return []
  }
  const data =
    typeof action.action_id === "string"
      ? action.action_id
      : typeof action.value === "string"
        ? action.value
        : ""
  const channelObj = isRecord(payload.channel) ? payload.channel : {}
  const message = isRecord(payload.message) ? payload.message : {}
  const user = isRecord(payload.user) ? payload.user : {}
  const channel = channelObj.id !== undefined ? String(channelObj.id) : ""
  const threadTs = typeof message.thread_ts === "string" ? message.thread_ts : ""
  return [
    {
      kind: "action",
      ...actionDefaults,
      thread_id: encodeThreadId({ channel, threadTs }),
      data,
      sender: user.id !== undefined ? String(user.id) : "",
      raw: asJsonObject(payload),
    },
  ]
}

const payloadOf = (
  raw: unknown,
): { readonly ok: true; readonly value: Record<string, unknown> | undefined } | {
  readonly ok: false
  readonly error: DecodeError
} => {
  if (typeof raw === "string" && raw.includes("payload=")) {
    try {
      const params = new URLSearchParams(raw)
      const payload = params.get("payload")
      if (payload === null) {
        return { ok: true, value: undefined }
      }
      return jsonObjectOf(payload)
    } catch (cause) {
      return {
        ok: false,
        error: new DecodeError({
          reason: `Invalid form body: ${cause instanceof Error ? cause.message : String(cause)}`,
        }),
      }
    }
  }
  return jsonObjectOf(raw)
}

export const parseSlackUpdate = (
  raw: unknown,
): Effect.Effect<ReadonlyArray<Event>, DecodeError> => {
  const decoded = payloadOf(raw)
  if (!decoded.ok) {
    return Effect.fail(decoded.error)
  }
  const payload = decoded.value
  if (payload === undefined) {
    return Effect.succeed([])
  }
  if (payload.type === "url_verification") {
    return Effect.succeed([])
  }
  if (payload.type === "event_callback") {
    return Effect.succeed(parseEvent(payload.event))
  }
  if (payload.type === "block_actions") {
    return Effect.succeed(parseBlockActions(payload))
  }
  return Effect.succeed([])
}
