import * as Schema from "effect/Schema"
import { ThreadId } from "../../core/ids.ts"

export type NativeThread = {
  readonly channel: string
  readonly threadTs?: string
}

const PREFIX = "slack:"

export const encodeThreadId = (native: NativeThread): ThreadId => {
  const rest =
    native.threadTs !== undefined && native.threadTs.length > 0
      ? `${native.channel}:${native.threadTs}`
      : native.channel
  return Schema.decodeUnknownSync(ThreadId)(`${PREFIX}${rest}`)
}

export const decodeThreadId = (
  threadId: ThreadId | string,
): { readonly channel: string; readonly threadTs: string } => {
  const value = String(threadId)
  const rest = value.startsWith(PREFIX) ? value.slice(PREFIX.length) : value
  const cut = rest.indexOf(":")
  if (cut < 0) {
    return { channel: rest, threadTs: "" }
  }
  return { channel: rest.slice(0, cut), threadTs: rest.slice(cut + 1) }
}

export const overlapKey = (event: {
  readonly thread_id: ThreadId | string
}): string => String(event.thread_id)
