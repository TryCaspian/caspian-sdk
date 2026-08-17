import type { Command, PostAction } from "../core/commands.ts"
import type { Event } from "../core/events.ts"
import type { ThreadId } from "../core/ids.ts"
import type { Json } from "../core/json.ts"

export type CommandSink = (command: Command) => void

const sinks = new WeakMap<object, CommandSink>()

export type ThreadMemory = {
  readonly recent: (
    limit: number,
    current: Event,
  ) => Promise<ReadonlyArray<Event>>
  readonly getState: (key: string) => Promise<Json | undefined>
  readonly setState: (key: string, value: Json) => Promise<void>
}

export type Thread = {
  readonly id: ThreadId
  post(
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  typing(): Promise<void>
  edit(messageId: string, text: string): Promise<void>
  react(messageId: string, emoji: string): Promise<void>
  recent(limit?: number): Promise<ReadonlyArray<Event>>
  readonly state: {
    get(key: string): Promise<Json | undefined>
    set(key: string, value: Json): Promise<void>
  }
}

export const makeThread = (
  id: ThreadId,
  sink: CommandSink,
  memory?: ThreadMemory,
  current?: Event,
): Thread => {
  const thread: Thread = {
    id,
    post: async (text, options) => {
      sink({
        tag: "Post",
        thread_id: id,
        text,
        actions: options?.actions === undefined ? [] : [...options.actions],
      })
    },
    typing: async () => {
      sink({ tag: "Typing", thread_id: id })
    },
    edit: async (messageId, text) => {
      sink({ tag: "Edit", thread_id: id, message_id: messageId, text })
    },
    react: async (messageId, emoji) => {
      sink({ tag: "React", thread_id: id, message_id: messageId, emoji })
    },
    recent: async (limit = 20) => {
      if (memory === undefined || current === undefined) {
        return []
      }
      return memory.recent(limit, current)
    },
    state: {
      get: async (key) => {
        if (memory === undefined) {
          return undefined
        }
        return memory.getState(key)
      },
      set: async (key, value) => {
        sink({ tag: "SetState", thread_id: id, key, value })
        if (memory !== undefined) {
          await memory.setState(key, value)
        }
      },
    },
  }
  sinks.set(thread, sink)
  return thread
}

export const enqueueCommand = (thread: Thread, command: Command): void => {
  const sink = sinks.get(thread)
  if (sink === undefined) {
    throw new Error("thread has no command sink")
  }
  sink(command)
}
