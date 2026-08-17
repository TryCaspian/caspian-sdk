import type { Attachment, Block } from "../../core/events.ts"
import type { Command, PostAction } from "../../core/commands.ts"
import type { Event } from "../../core/events.ts"
import { emptySent, type Sent } from "../../core/ports.ts"
import type { Connection } from "../../core/connection.ts"
import { AdapterError } from "../../core/errors.ts"
import type { JsonObject } from "../../core/json.ts"
import type { HttpJsonCall } from "../plan.ts"
import { configString } from "../util.ts"
import { decodeThreadId } from "./ids.ts"

export type TelegramCall = {
  readonly method: string
  readonly body: { readonly [key: string]: unknown }
}

const API_BASE = "https://api.telegram.org"

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const callbackQueryId = (event: Event): string | undefined => {
  if (event.kind !== "action") {
    return undefined
  }
  if (event.interaction_id.length > 0) {
    return event.interaction_id
  }
  const query = event.raw["callback_query"]
  if (!isRecord(query) || typeof query.id !== "string") {
    return undefined
  }
  return query.id
}

const buttonText = (action: PostAction): string =>
  action.text ?? action.label ?? "ok"

const buttonData = (action: PostAction): string =>
  action.data ?? action.value ?? action.text ?? ""

const chatOf = (threadId: string): string => decodeThreadId(threadId).chatId

const topicOf = (threadId: string): string => {
  const rest = chatOf(threadId)
  const parts = rest.split(":")
  return parts.length > 1 ? (parts[1] ?? "") : ""
}

const chatIdOnly = (threadId: string): string => {
  const rest = chatOf(threadId)
  return rest.split(":")[0] ?? rest
}

const keyboard = (
  actions: ReadonlyArray<PostAction>,
): { readonly inline_keyboard: ReadonlyArray<ReadonlyArray<unknown>> } => ({
  inline_keyboard: [
    actions.map((action) => {
      if (action.url !== undefined && action.url.length > 0) {
        return { text: buttonText(action), url: action.url }
      }
      return { text: buttonText(action), callback_data: buttonData(action) }
    }),
  ],
})

const blocksToText = (blocks: ReadonlyArray<Block>): string =>
  blocks
    .map((block) => {
      const text = block.content["text"]
      return typeof text === "string" ? text : ""
    })
    .filter((line) => line.length > 0)
    .join("\n")

const msgBody = (
  threadId: string,
  text: string,
  actions: ReadonlyArray<PostAction>,
): { [key: string]: unknown } => {
  const body: { [key: string]: unknown } = {
    chat_id: chatIdOnly(threadId),
    text,
  }
  const topic = topicOf(threadId)
  if (topic.length > 0) {
    body.message_thread_id = /^\d+$/.test(topic) ? Number(topic) : topic
  }
  if (actions.length > 0) {
    body.reply_markup = keyboard(actions)
  }
  return body
}

const mediaCall = (
  threadId: string,
  attachment: Attachment,
  caption: string,
): TelegramCall => {
  const methodMap: { readonly [key: string]: readonly [string, string] } = {
    photo: ["sendPhoto", "photo"],
    file: ["sendDocument", "document"],
    audio: ["sendAudio", "audio"],
    voice: ["sendVoice", "voice"],
    video: ["sendVideo", "video"],
    sticker: ["sendSticker", "sticker"],
  }
  const pair = methodMap[attachment.type] ?? ["sendDocument", "document"]
  const method = pair[0]
  const field = pair[1]
  const body: { [key: string]: unknown } = {
    chat_id: chatIdOnly(threadId),
    [field]: attachment.url.length > 0 ? attachment.url : attachment.file_id,
  }
  const cap = caption.length > 0 ? caption : attachment.caption
  if (cap.length > 0) {
    body.caption = cap
  }
  return { method, body }
}

export const planAck = (event: Event): TelegramCall | undefined => {
  const id = callbackQueryId(event)
  if (id === undefined) {
    return undefined
  }
  return {
    method: "answerCallbackQuery",
    body: { callback_query_id: id },
  }
}

export const planPoll = (offset: number): TelegramCall => ({
  method: "getUpdates",
  body: { offset, timeout: 0 },
})

export const planCommand = (command: Command): TelegramCall | undefined => {
  switch (command.tag) {
    case "Post":
    case "Initiate":
      return { method: "sendMessage", body: msgBody(command.thread_id, command.text, command.actions) }
    case "Reply": {
      const body = msgBody(command.thread_id, command.text, command.actions)
      if (/^\d+$/.test(command.reply_to)) {
        body.reply_parameters = { message_id: Number(command.reply_to) }
      }
      return { method: "sendMessage", body }
    }
    case "SendBlocks": {
      const rendered =
        command.text.length > 0 ? command.text : blocksToText(command.blocks)
      return {
        method: "sendMessage",
        body: msgBody(command.thread_id, rendered, command.actions),
      }
    }
    case "SendMedia":
      return mediaCall(command.thread_id, command.attachment, command.caption)
    case "Edit": {
      const body: { [key: string]: unknown } = {
        chat_id: chatIdOnly(command.thread_id),
        message_id: command.message_id,
        text: command.text,
      }
      if (command.actions.length > 0) {
        body.reply_markup = keyboard(command.actions)
      }
      return { method: "editMessageText", body }
    }
    case "Delete":
      return {
        method: "deleteMessage",
        body: {
          chat_id: chatIdOnly(command.thread_id),
          message_id: command.message_id,
        },
      }
    case "Typing":
      return {
        method: "sendChatAction",
        body: {
          chat_id: chatIdOnly(command.thread_id),
          action: "typing",
        },
      }
    case "React":
      return {
        method: "setMessageReaction",
        body: {
          chat_id: chatIdOnly(command.thread_id),
          message_id: command.message_id,
          reaction: [{ type: "emoji", emoji: command.emoji }],
        },
      }
    case "Pin":
      return {
        method: "pinChatMessage",
        body: {
          chat_id: chatIdOnly(command.thread_id),
          message_id: command.message_id,
        },
      }
    case "Unpin":
      return {
        method: "unpinChatMessage",
        body: {
          chat_id: chatIdOnly(command.thread_id),
          message_id: command.message_id,
        },
      }
    case "Forward":
      return {
        method: "forwardMessage",
        body: {
          from_chat_id: chatIdOnly(command.from_thread_id),
          chat_id: chatIdOnly(command.to_thread_id),
          message_id: command.message_id,
        },
      }
    case "Call":
      return { method: command.method, body: { ...command.args } }
    case "MarkRead":
    case "Host":
    case "Subscribe":
    case "SetState":
    case "ScheduleSend":
    case "ListHistory":
    case "OpenModal":
    case "UpdateModal":
      return undefined
  }
}

export const planTurn = (
  event: Event,
  commands: ReadonlyArray<Command>,
): ReadonlyArray<TelegramCall> => {
  const calls: TelegramCall[] = []
  const ack = planAck(event)
  if (ack !== undefined) {
    calls.push(ack)
  }
  for (const command of commands) {
    const planned = planCommand(command)
    if (planned !== undefined) {
      calls.push(planned)
    }
  }
  return calls
}

export const asHttpJson = (
  call: TelegramCall,
  token: string,
): HttpJsonCall => ({
  transport: "http_json",
  method: "POST",
  url: `${API_BASE}/bot${token}/${call.method}`,
  json: call.body,
  native: call.method,
})

export const telegramSent = (
  call: TelegramCall | undefined,
  token: string,
): Sent => {
  if (call === undefined) {
    return emptySent()
  }
  const planned = asHttpJson(call, token)
  return {
    ok: true,
    message_id: "",
    raw: JSON.parse(JSON.stringify(planned)) as JsonObject,
  }
}

export const markReadSent = (): Sent => ({
  ok: true,
  message_id: "",
  raw: { transport: "noop", native: "markRead" } as JsonObject,
})

export const telegramCommandError = (command: Command): AdapterError | undefined => {
  if (command.tag === "ScheduleSend") {
    return new AdapterError({
      reason: "Telegram cannot schedule server-side; use the runner's scheduler",
      commandTag: "ScheduleSend",
    })
  }
  if (command.tag === "ListHistory") {
    return new AdapterError({
      reason: "Telegram Bot API cannot backfill history (needs MTProto)",
      commandTag: "ListHistory",
    })
  }
  if (command.tag === "OpenModal" || command.tag === "UpdateModal") {
    return new AdapterError({
      reason: `Unsupported command: ${command.tag}`,
      commandTag: command.tag,
    })
  }
  return undefined
}

export const tokenOf = (conn: Connection): string =>
  configString(conn.config, "botToken")

