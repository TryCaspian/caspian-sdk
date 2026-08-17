import * as Effect from "effect/Effect"
import type { Attachment, Block } from "../core/events.ts"
import type { Command, PostAction } from "../core/commands.ts"
import type { Event } from "../core/events.ts"
import type { ThreadId } from "../core/ids.ts"
import type { Json } from "../core/json.ts"
import type { StreamSink } from "../core/ports.ts"

export type CommandSink = (command: Command) => void

const sinks = new WeakMap<object, CommandSink>()
const lives = new WeakMap<object, StreamSink>()

export type ThreadMemory = {
  readonly recent: (
    limit: number,
    current: Event,
  ) => Promise<ReadonlyArray<Event>>
  readonly getState: (key: string) => Promise<Json | undefined>
  readonly setState: (key: string, value: Json) => Promise<void>
}

export type Stream = {
  readonly text: string
  readonly live: boolean
  append(chunk: string): Promise<void>
  close(): Promise<void>
}

export type Thread = {
  readonly id: ThreadId
  post(
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  /** Send without threading onto the message that triggered this turn. */
  send(
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  reply(
    replyTo: string,
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  sendBlocks(
    blocks: ReadonlyArray<Block>,
    options?: {
      readonly text?: string
      readonly actions?: ReadonlyArray<PostAction>
    },
  ): Promise<void>
  send_blocks: Thread["sendBlocks"]
  sendMedia(
    attachment: Attachment,
    options?: { readonly caption?: string },
  ): Promise<void>
  send_media: Thread["sendMedia"]
  typing(): Promise<void>
  edit(
    messageId: string,
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  delete(messageId: string): Promise<void>
  react(messageId: string, emoji: string): Promise<void>
  pin(messageId: string): Promise<void>
  unpin(messageId: string): Promise<void>
  forward(toThreadId: ThreadId, messageId: string): Promise<void>
  markRead(messageId?: string): Promise<void>
  mark_read: Thread["markRead"]
  initiate(
    text: string,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  schedule(
    text: string,
    sendAt: number,
    options?: { readonly actions?: ReadonlyArray<PostAction> },
  ): Promise<void>
  history(options?: {
    readonly limit?: number
    readonly before?: string
  }): Promise<void>
  subscribe(): Promise<void>
  stream(options?: { readonly minChars?: number }): Stream
  recent(limit?: number): Promise<ReadonlyArray<Event>>
  readonly state: {
    get(key: string): Promise<Json | undefined>
    set(key: string, value: Json): Promise<void>
  }
}

const actionsOf = (
  options?: { readonly actions?: ReadonlyArray<PostAction> },
): PostAction[] => (options?.actions === undefined ? [] : [...options.actions])

export const makeThread = (
  id: ThreadId,
  sink: CommandSink,
  memory?: ThreadMemory,
  current?: Event,
  live?: StreamSink,
): Thread => {
  const thread = {
    id,
    post: async (
      text: string,
      options?: { readonly actions?: ReadonlyArray<PostAction> },
    ) => {
      sink({
        tag: "Post",
        thread_id: id,
        text,
        actions: actionsOf(options),
        standalone: false,
      })
    },
    /**
     * Send WITHOUT threading, even mid-conversation.
     *
     * post() answers the message that triggered the turn; use this for an
     * unprompted message that should start its own thread.
     */
    send: async (
      text: string,
      options?: { readonly actions?: ReadonlyArray<PostAction> },
    ) => {
      sink({
        tag: "Post",
        thread_id: id,
        text,
        actions: actionsOf(options),
        standalone: true,
      })
    },
    reply: async (
      replyTo: string,
      text: string,
      options?: { readonly actions?: ReadonlyArray<PostAction> },
    ) => {
      sink({
        tag: "Reply",
        thread_id: id,
        reply_to: replyTo,
        text,
        actions: actionsOf(options),
      })
    },
    sendBlocks: async (
      blocks: ReadonlyArray<Block>,
      options?: {
        readonly text?: string
        readonly actions?: ReadonlyArray<PostAction>
      },
    ) => {
      sink({
        tag: "SendBlocks",
        thread_id: id,
        blocks: [...blocks],
        text: options?.text ?? "",
        actions: actionsOf(options),
      })
    },
    sendMedia: async (
      attachment: Attachment,
      options?: { readonly caption?: string },
    ) => {
      sink({
        tag: "SendMedia",
        thread_id: id,
        attachment,
        caption: options?.caption ?? "",
      })
    },
    typing: async () => {
      sink({ tag: "Typing", thread_id: id })
    },
    edit: async (
      messageId: string,
      text: string,
      options?: { readonly actions?: ReadonlyArray<PostAction> },
    ) => {
      sink({
        tag: "Edit",
        thread_id: id,
        message_id: messageId,
        text,
        actions: actionsOf(options),
      })
    },
    delete: async (messageId: string) => {
      sink({ tag: "Delete", thread_id: id, message_id: messageId })
    },
    react: async (messageId: string, emoji: string) => {
      sink({ tag: "React", thread_id: id, message_id: messageId, emoji })
    },
    pin: async (messageId: string) => {
      sink({ tag: "Pin", thread_id: id, message_id: messageId })
    },
    unpin: async (messageId: string) => {
      sink({ tag: "Unpin", thread_id: id, message_id: messageId })
    },
    forward: async (toThreadId: ThreadId, messageId: string) => {
      sink({
        tag: "Forward",
        from_thread_id: id,
        to_thread_id: toThreadId,
        message_id: messageId,
      })
    },
    markRead: async (messageId = "") => {
      sink({ tag: "MarkRead", thread_id: id, message_id: messageId })
    },
    initiate: async (
      text: string,
      options?: { readonly actions?: ReadonlyArray<PostAction> },
    ) => {
      sink({
        tag: "Initiate",
        thread_id: id,
        text,
        actions: actionsOf(options),
      })
    },
    schedule: async (
      text: string,
      sendAt: number,
      options?: { readonly actions?: ReadonlyArray<PostAction> },
    ) => {
      sink({
        tag: "ScheduleSend",
        thread_id: id,
        text,
        send_at: sendAt,
        actions: actionsOf(options),
      })
    },
    history: async (options?: {
      readonly limit?: number
      readonly before?: string
    }) => {
      sink({
        tag: "ListHistory",
        thread_id: id,
        limit: options?.limit ?? 20,
        before: options?.before ?? "",
      })
    },
    subscribe: async () => {
      sink({ tag: "Subscribe", thread_id: id })
    },
    stream: (options?: { readonly minChars?: number }) =>
      makeStream(thread as Thread, options?.minChars ?? 24),
    recent: async (limit = 20) => {
      if (memory === undefined || current === undefined) {
        return []
      }
      return memory.recent(limit, current)
    },
    state: {
      get: async (key: string) => {
        if (memory === undefined) {
          return undefined
        }
        return memory.getState(key)
      },
      set: async (key: string, value: Json) => {
        sink({ tag: "SetState", thread_id: id, key, value })
        if (memory !== undefined) {
          await memory.setState(key, value)
        }
      },
    },
  }
  const withAliases = thread as Thread
  withAliases.send_blocks = withAliases.sendBlocks
  withAliases.send_media = withAliases.sendMedia
  withAliases.mark_read = withAliases.markRead
  sinks.set(withAliases, sink)
  if (live !== undefined) {
    lives.set(withAliases, live)
  }
  return withAliases
}

const makeStream = (thread: Thread, minChars: number): Stream => {
  let text = ""
  let sent = ""
  let messageId = ""
  let closed = false
  let liveSink = lives.get(thread)

  const flushLive = async (): Promise<void> => {
    if (text === sent || liveSink === undefined) {
      return
    }
    if (messageId.length === 0) {
      messageId = await Effect.runPromise(
        liveSink.emit({
          tag: "Post",
          thread_id: thread.id,
          text,
          actions: [],
          standalone: false,
        }),
      )
      if (messageId.length === 0) {
        liveSink = undefined
        sent = text
        return
      }
    } else {
      await Effect.runPromise(
        liveSink.emit({
          tag: "Edit",
          thread_id: thread.id,
          message_id: messageId,
          text,
          actions: [],
        }),
      )
    }
    sent = text
  }

  const stream: Stream = {
    get text() {
      return text
    },
    get live() {
      return liveSink !== undefined && liveSink.can_stream
    },
    append: async (chunk) => {
      if (closed || chunk.length === 0) {
        return
      }
      text += chunk
      if (stream.live && text.length - sent.length >= minChars) {
        await flushLive()
      }
    },
    close: async () => {
      if (closed) {
        return
      }
      closed = true
      if (text.length === 0) {
        return
      }
      if (stream.live) {
        await flushLive()
        return
      }
      await thread.post(text)
    },
  }
  return stream
}

export const enqueueCommand = (thread: Thread, command: Command): void => {
  const sink = sinks.get(thread)
  if (sink === undefined) {
    throw new Error("thread has no command sink")
  }
  sink(command)
}
