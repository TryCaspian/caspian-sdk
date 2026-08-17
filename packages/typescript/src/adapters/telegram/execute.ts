import type { Command, PostAction } from "../../core/commands.ts"
import type { Event } from "../../core/events.ts"
import { decodeThreadId } from "./ids.ts"

export type TelegramCall = {
  readonly method: string
  readonly body: { readonly [key: string]: unknown }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const callbackQueryId = (event: Event): string | undefined => {
  if (event.kind !== "action") {
    return undefined
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

export const planCommand = (command: Command): TelegramCall | undefined => {
  switch (command.tag) {
    case "Post": {
      const body: { [key: string]: unknown } = {
        chat_id: decodeThreadId(command.thread_id).chatId,
        text: command.text,
      }
      if (command.actions.length > 0) {
        body.reply_markup = {
          inline_keyboard: [
            command.actions.map((action) => ({
              text: buttonText(action),
              callback_data: buttonData(action),
            })),
          ],
        }
      }
      return { method: "sendMessage", body }
    }
    case "Edit":
      return {
        method: "editMessageText",
        body: {
          chat_id: decodeThreadId(command.thread_id).chatId,
          message_id: command.message_id,
          text: command.text,
        },
      }
    case "Typing":
      return {
        method: "sendChatAction",
        body: {
          chat_id: decodeThreadId(command.thread_id).chatId,
          action: "typing",
        },
      }
    case "React":
      return {
        method: "setMessageReaction",
        body: {
          chat_id: decodeThreadId(command.thread_id).chatId,
          message_id: command.message_id,
          reaction: [{ type: "emoji", emoji: command.emoji }],
        },
      }
    case "Host":
    case "Subscribe":
    case "SetState":
    case "Call":
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
