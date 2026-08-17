import type { ChatKind, Event } from "../../core/events.ts"
import { ThreadId } from "../../core/ids.ts"
import type { JsonObject } from "../../core/json.ts"
import { encodeThreadId } from "./ids.ts"

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const asJsonObject = (value: unknown): JsonObject => {
  try {
    const parsed: unknown = JSON.parse(JSON.stringify(value))
    if (isRecord(parsed)) {
      return parsed as JsonObject
    }
  } catch {
    return {}
  }
  return {}
}

const chatKindOf = (type: unknown): ChatKind => {
  if (type === "private") {
    return "dm"
  }
  if (type === "channel") {
    return "channel"
  }
  return "group"
}

const senderOf = (from: unknown): string => {
  if (!isRecord(from)) {
    return ""
  }
  if (typeof from.username === "string" && from.username.length > 0) {
    return from.username
  }
  if (from.id !== undefined && from.id !== null) {
    return String(from.id)
  }
  return ""
}

const chatIdOf = (chat: unknown): string | undefined => {
  if (!isRecord(chat) || chat.id === undefined || chat.id === null) {
    return undefined
  }
  return String(chat.id)
}

const threadIdOf = (chat: unknown): ThreadId | undefined => {
  const chatId = chatIdOf(chat)
  if (chatId === undefined) {
    return undefined
  }
  return encodeThreadId({ chatId })
}

const textOf = (message: Record<string, unknown>): string => {
  if (typeof message.text === "string") {
    return message.text
  }
  if (typeof message.caption === "string") {
    return message.caption
  }
  return ""
}

const firstEmoji = (reactions: unknown): string => {
  if (!Array.isArray(reactions)) {
    return ""
  }
  const first = reactions[0]
  if (isRecord(first) && typeof first.emoji === "string") {
    return first.emoji
  }
  return ""
}

export const parseTelegramUpdate = (raw: unknown): ReadonlyArray<Event> => {
  if (!isRecord(raw)) {
    return []
  }
  const payload = asJsonObject(raw)

  if (isRecord(raw.message)) {
    const threadId = threadIdOf(raw.message.chat)
    if (threadId === undefined) {
      return []
    }
    const chat = isRecord(raw.message.chat) ? raw.message.chat : {}
    return [
      {
        kind: "message",
        thread_id: threadId,
        text: textOf(raw.message),
        chat_kind: chatKindOf(chat.type),
        sender: senderOf(raw.message.from),
        raw: payload,
      },
    ]
  }

  if (isRecord(raw.callback_query)) {
    const message = isRecord(raw.callback_query.message)
      ? raw.callback_query.message
      : {}
    const threadId = threadIdOf(message.chat)
    if (threadId === undefined) {
      return []
    }
    return [
      {
        kind: "action",
        thread_id: threadId,
        data:
          typeof raw.callback_query.data === "string"
            ? raw.callback_query.data
            : "",
        sender: senderOf(raw.callback_query.from),
        raw: payload,
      },
    ]
  }

  if (isRecord(raw.message_reaction)) {
    const threadId = threadIdOf(raw.message_reaction.chat)
    if (threadId === undefined) {
      return []
    }
    return [
      {
        kind: "reaction",
        thread_id: threadId,
        emoji: firstEmoji(raw.message_reaction.new_reaction),
        sender: senderOf(raw.message_reaction.user),
        raw: payload,
      },
    ]
  }

  return []
}
