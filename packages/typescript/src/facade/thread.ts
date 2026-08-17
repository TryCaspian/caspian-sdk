import type { Command, PostAction } from "../core/commands.ts"
import type { ThreadId } from "../core/ids.ts"

export type CommandSink = (command: Command) => void

const sinks = new WeakMap<object, CommandSink>()

export type Thread = {
  readonly id: ThreadId
  post(
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  typing(): Promise<void>
  edit(messageId: string, text: string): Promise<void>
  react(messageId: string, emoji: string): Promise<void>
}

export const makeThread = (id: ThreadId, sink: CommandSink): Thread => {
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
