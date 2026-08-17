import { ThreadId } from "../../core/ids.ts"
import { encodePrefixed, suffixAfter } from "../util.ts"

export type NativeThread = {
  readonly channelId: string
}

const PREFIX = "discord:"

export const encodeThreadId = (native: NativeThread): ThreadId =>
  encodePrefixed(PREFIX, native.channelId)

export const decodeThreadId = (threadId: ThreadId | string): NativeThread => ({
  channelId: suffixAfter(String(threadId), PREFIX),
})

export const overlapKey = (event: {
  readonly thread_id: ThreadId | string
}): string => String(event.thread_id)
