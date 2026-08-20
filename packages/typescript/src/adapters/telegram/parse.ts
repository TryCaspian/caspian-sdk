import type { Attachment, ChatKind, Event } from "../../core/events.ts"
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

const attachmentsOf = (message: Record<string, unknown>): ReadonlyArray<Attachment> => {
  const out: Attachment[] = []
  const caption = typeof message.caption === "string" ? message.caption : ""
  if (Array.isArray(message.photo) && message.photo.length > 0) {
    const largest = message.photo[message.photo.length - 1]
    if (isRecord(largest)) {
      out.push({
        type: "photo",
        url: "",
        file_id: typeof largest.file_id === "string" ? largest.file_id : "",
        filename: "",
        mime_type: "",
        size_bytes: typeof largest.file_size === "number" ? largest.file_size : 0,
        caption,
      })
    }
  }
  if (isRecord(message.document)) {
    const doc = message.document
    out.push({
      type: "file",
      url: "",
      file_id: typeof doc.file_id === "string" ? doc.file_id : "",
      filename: typeof doc.file_name === "string" ? doc.file_name : "",
      mime_type: typeof doc.mime_type === "string" ? doc.mime_type : "",
      size_bytes: typeof doc.file_size === "number" ? doc.file_size : 0,
      caption,
    })
  }
  if (isRecord(message.audio) || isRecord(message.voice)) {
    const media = isRecord(message.voice) ? message.voice : message.audio
    if (isRecord(media)) {
      out.push({
        type: isRecord(message.voice) ? "voice" : "audio",
        url: "",
        file_id: typeof media.file_id === "string" ? media.file_id : "",
        filename: "",
        mime_type: typeof media.mime_type === "string" ? media.mime_type : "",
        size_bytes: typeof media.file_size === "number" ? media.file_size : 0,
        caption: "",
      })
    }
  }
  if (isRecord(message.video)) {
    const video = message.video
    out.push({
      type: "video",
      url: "",
      file_id: typeof video.file_id === "string" ? video.file_id : "",
      filename: "",
      mime_type: typeof video.mime_type === "string" ? video.mime_type : "",
      size_bytes: typeof video.file_size === "number" ? video.file_size : 0,
      caption,
    })
  }
  return out
}

const parseMessage = (
  message: Record<string, unknown>,
  payload: JsonObject,
): ReadonlyArray<Event> => {
  const threadId = threadIdOf(message.chat)
  if (threadId === undefined) {
    return []
  }
  const chat = isRecord(message.chat) ? message.chat : {}
  if (Array.isArray(message.new_chat_members)) {
    return message.new_chat_members.flatMap((member) => {
      if (!isRecord(member)) {
        return []
      }
      return [
        {
          kind: "member_join" as const,
          thread_id: threadId,
          member: member.id !== undefined && member.id !== null ? String(member.id) : "",
          chat_kind: chatKindOf(chat.type),
          raw: payload,
        },
      ]
    })
  }
  if (isRecord(message.left_chat_member)) {
    const left = message.left_chat_member
    return [
      {
        kind: "member_leave",
        thread_id: threadId,
        member: left.id !== undefined && left.id !== null ? String(left.id) : "",
        chat_kind: chatKindOf(chat.type),
        raw: payload,
      },
    ]
  }
  const replyTo = isRecord(message.reply_to_message)
    ? String(message.reply_to_message.message_id ?? "")
    : ""
  const topicId =
    message.is_topic_message === true && message.message_thread_id !== undefined
      ? String(message.message_thread_id)
      : ""
  return [
    {
      kind: "message",
      thread_id: threadId,
      text: textOf(message),
      chat_kind: chatKindOf(chat.type),
      sender: senderOf(message.from),
      message_id:
        message.message_id !== undefined && message.message_id !== null
          ? String(message.message_id)
          : "",
      attachments: [...attachmentsOf(message)],
      blocks: [],
      reply_to: replyTo,
      topic_id: topicId,
      metadata: {},
      raw: payload,
    },
  ]
}

const parseEdited = (
  message: Record<string, unknown>,
  payload: JsonObject,
): ReadonlyArray<Event> => {
  const threadId = threadIdOf(message.chat)
  if (threadId === undefined) {
    return []
  }
  return [
    {
      kind: "edited",
      thread_id: threadId,
      message_id:
        message.message_id !== undefined && message.message_id !== null
          ? String(message.message_id)
          : "",
      text: textOf(message),
      sender: senderOf(message.from),
      raw: payload,
    },
  ]
}

export const parseTelegramUpdate = (raw: unknown): ReadonlyArray<Event> => {
  if (!isRecord(raw)) {
    return []
  }
  const payload = asJsonObject(raw)

  if (isRecord(raw.message)) {
    return parseMessage(raw.message, payload)
  }

  if (isRecord(raw.edited_message)) {
    return parseEdited(raw.edited_message, payload)
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
        message_id:
          message.message_id !== undefined && message.message_id !== null
            ? String(message.message_id)
            : "",
        interaction_id:
          typeof raw.callback_query.id === "string" ? raw.callback_query.id : "",
        metadata: {},
        raw: payload,
      },
    ]
  }

  if (isRecord(raw.message_reaction)) {
    const threadId = threadIdOf(raw.message_reaction.chat)
    if (threadId === undefined) {
      return []
    }
    const next = raw.message_reaction.new_reaction
    return [
      {
        kind: "reaction",
        thread_id: threadId,
        emoji: firstEmoji(next),
        sender: senderOf(raw.message_reaction.user),
        message_id:
          raw.message_reaction.message_id !== undefined &&
          raw.message_reaction.message_id !== null
            ? String(raw.message_reaction.message_id)
            : "",
        removed: Array.isArray(next) ? next.length === 0 : false,
        raw: payload,
      },
    ]
  }

  return []
}
