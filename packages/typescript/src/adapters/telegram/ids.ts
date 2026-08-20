import * as Schema from "effect/Schema"
import { ThreadId } from "../../core/ids.ts"

export type NativeThread = {
  readonly chatId: string
}

const PREFIX = "telegram:"

export const encodeThreadId = (native: NativeThread): ThreadId =>
  Schema.decodeUnknownSync(ThreadId)(`${PREFIX}${native.chatId}`)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => {
  const value = String(threadId)
  const chatId = value.startsWith(PREFIX) ? value.slice(PREFIX.length) : value
  return { chatId }
}

export const overlapKey = (event: { readonly thread_id: ThreadId | string }): string =>
  decodeThreadId(event.thread_id).chatId
