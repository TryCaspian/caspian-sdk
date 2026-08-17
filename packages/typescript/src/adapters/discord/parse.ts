import * as Effect from "effect/Effect"
import type { Event } from "../../core/events.ts"
import { DecodeError } from "../../core/errors.ts"
import { asJsonObject, isRecord, jsonObjectOf } from "../util.ts"
import { encodeThreadId } from "./ids.ts"

const PING = 1
const APPLICATION_COMMAND = 2
const MESSAGE_COMPONENT = 3

const asList = (value: unknown): ReadonlyArray<Record<string, unknown>> => {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter(isRecord)
}

const senderOf = (payload: Record<string, unknown>): string => {
  const member = isRecord(payload.member) ? payload.member : {}
  const user = isRecord(member.user)
    ? member.user
    : isRecord(payload.user)
      ? payload.user
      : isRecord(payload.author)
        ? payload.author
        : {}
  return user.id !== undefined && user.id !== null ? String(user.id) : ""
}

const commandText = (data: Record<string, unknown>): string => {
  const parts = asList(data.options)
    .map((opt) => opt.value)
    .filter((value) => value !== undefined && value !== null)
    .map((value) => String(value))
  if (parts.length > 0) {
    return parts.join(" ")
  }
  return typeof data.name === "string" ? data.name : ""
}

const parseCommand = (payload: Record<string, unknown>): ReadonlyArray<Event> => {
  const channelId =
    payload.channel_id !== undefined && payload.channel_id !== null
      ? String(payload.channel_id)
      : ""
  const data = isRecord(payload.data) ? payload.data : {}
  return [
    {
      kind: "message",
      thread_id: encodeThreadId({ channelId }),
      text: commandText(data),
      chat_kind: "channel",
      sender: senderOf(payload),
      raw: asJsonObject(payload),
    },
  ]
}

const parseComponent = (
  payload: Record<string, unknown>,
): ReadonlyArray<Event> => {
  const channelId =
    payload.channel_id !== undefined && payload.channel_id !== null
      ? String(payload.channel_id)
      : ""
  const data = isRecord(payload.data) ? payload.data : {}
  return [
    {
      kind: "action",
      thread_id: encodeThreadId({ channelId }),
      data: typeof data.custom_id === "string" ? data.custom_id : "",
      sender: senderOf(payload),
      raw: asJsonObject(payload),
    },
  ]
}

const parseMessageCreate = (
  payload: Record<string, unknown>,
): ReadonlyArray<Event> => {
  const channelId =
    payload.channel_id !== undefined && payload.channel_id !== null
      ? String(payload.channel_id)
      : ""
  const author = isRecord(payload.author) ? payload.author : {}
  return [
    {
      kind: "message",
      thread_id: encodeThreadId({ channelId }),
      text: typeof payload.content === "string" ? payload.content : "",
      chat_kind: "channel",
      sender: author.id !== undefined && author.id !== null ? String(author.id) : "",
      raw: asJsonObject(payload),
    },
  ]
}

const parseReaction = (
  payload: Record<string, unknown>,
): ReadonlyArray<Event> => {
  const channelId =
    payload.channel_id !== undefined && payload.channel_id !== null
      ? String(payload.channel_id)
      : ""
  const emoji = isRecord(payload.emoji) ? payload.emoji : {}
  return [
    {
      kind: "reaction",
      thread_id: encodeThreadId({ channelId }),
      emoji: typeof emoji.name === "string" ? emoji.name : "",
      sender:
        payload.user_id !== undefined && payload.user_id !== null
          ? String(payload.user_id)
          : "",
      raw: asJsonObject(payload),
    },
  ]
}

export const parseDiscordUpdate = (
  raw: unknown,
): Effect.Effect<ReadonlyArray<Event>, DecodeError> => {
  const decoded = jsonObjectOf(raw)
  if (!decoded.ok) {
    return Effect.fail(decoded.error)
  }
  const payload = decoded.value
  if (payload === undefined) {
    return Effect.succeed([])
  }
  if (payload.type === PING) {
    return Effect.succeed([])
  }
  if (payload.type === APPLICATION_COMMAND) {
    return Effect.succeed(parseCommand(payload))
  }
  if (payload.type === MESSAGE_COMPONENT) {
    return Effect.succeed(parseComponent(payload))
  }
  if ("emoji" in payload && "message_id" in payload) {
    return Effect.succeed(parseReaction(payload))
  }
  if ("content" in payload && "channel_id" in payload) {
    return Effect.succeed(parseMessageCreate(payload))
  }
  return Effect.succeed([])
}
