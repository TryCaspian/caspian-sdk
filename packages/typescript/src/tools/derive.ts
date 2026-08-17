/**
 * Agent tools — a view over A Commands. Not a second schema, not platform HTTP.
 */
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as JSONSchema from "effect/JSONSchema"
import * as Schema from "effect/Schema"
import { Command, Edit, Post, React, Typing } from "../core/commands.ts"
import type { DecodeError } from "../core/errors.ts"
import { decodeStrict } from "../core/parse.ts"
import {
  enqueueCommand,
  type Thread,
} from "../facade/thread.ts"

export type ToolPreset = "messenger" | "outbound"

export type CaspianTool = {
  readonly name: string
  readonly description: string
  readonly parameters: object
  readonly execute: (args: unknown) => Promise<Command>
}

export type ToolSet = {
  readonly post_message: CaspianTool
  readonly send_dm: CaspianTool
  readonly edit_message?: CaspianTool
  readonly add_reaction?: CaspianTool
  readonly start_typing?: CaspianTool
}

export type ToolsOptions = {
  readonly preset?: ToolPreset
}

const BoundPost = Post.pipe(Schema.omit("tag", "thread_id"))
const OutboundPost = Post.pipe(Schema.omit("tag"))
const SendDm = Post.pipe(Schema.omit("tag", "actions"))
const BoundEdit = Edit.pipe(Schema.omit("tag", "thread_id"))
const BoundReact = React.pipe(Schema.omit("tag", "thread_id"))
const BoundTyping = Typing.pipe(Schema.omit("tag", "thread_id"))

const unwrap = <A>(effect: Effect.Effect<A, DecodeError>): A => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isLeft(result)) {
    throw result.left
  }
  return result.right
}

const asCommand = (input: unknown): Command =>
  unwrap(decodeStrict(Command)(input))

const tool = <A, I>(
  name: string,
  description: string,
  parameters: Schema.Schema<A, I>,
  toCommand: (args: A) => Command,
  thread: Thread | undefined,
): CaspianTool => ({
  name,
  description,
  parameters: JSONSchema.make(parameters as Schema.Schema<unknown>),
  execute: async (args) => {
    const decoded = unwrap(decodeStrict(parameters)(args ?? {}))
    const command = toCommand(decoded)
    if (thread !== undefined) {
      enqueueCommand(thread, command)
    }
    return command
  },
})

const isThread = (value: unknown): value is Thread =>
  typeof value === "object" &&
  value !== null &&
  "id" in value &&
  "post" in value &&
  typeof (value as Thread).post === "function"

export const deriveTools = (
  thread: Thread | undefined,
  preset: ToolPreset,
): ToolSet => {
  const sendDm = tool(
    "send_dm",
    "Send a DM to a thread id (telegram:… / slack:…), never a chat id.",
    SendDm,
    (args) => asCommand({ tag: "Post", actions: [], ...args }),
    thread,
  )
  if (preset === "outbound" || thread === undefined) {
    return {
      post_message: tool(
        "post_message",
        "Post text to a thread. Address the thread id, never a platform chat id.",
        OutboundPost,
        (args) => asCommand({ tag: "Post", ...args }),
        thread,
      ),
      send_dm: sendDm,
    }
  }
  return {
    post_message: tool(
      "post_message",
      "Post text to a thread. Address the thread id, never a platform chat id.",
      BoundPost,
      (args) => asCommand({ tag: "Post", thread_id: thread.id, ...args }),
      thread,
    ),
    edit_message: tool(
      "edit_message",
      "Edit a message on a thread.",
      BoundEdit,
      (args) => asCommand({ tag: "Edit", thread_id: thread.id, ...args }),
      thread,
    ),
    add_reaction: tool(
      "add_reaction",
      "Add a reaction to a message.",
      BoundReact,
      (args) => asCommand({ tag: "React", thread_id: thread.id, ...args }),
      thread,
    ),
    start_typing: tool(
      "start_typing",
      "Show typing in a thread.",
      BoundTyping,
      (args) => asCommand({ tag: "Typing", thread_id: thread.id, ...args }),
      thread,
    ),
    send_dm: sendDm,
  }
}

export const splitToolsArgs = (
  threadOrOptions: Thread | ToolsOptions | undefined,
  maybeOptions: ToolsOptions | undefined,
): { thread: Thread | undefined; preset: ToolPreset } => {
  if (isThread(threadOrOptions)) {
    return {
      thread: threadOrOptions,
      preset: maybeOptions?.preset ?? "messenger",
    }
  }
  return {
    thread: undefined,
    preset: threadOrOptions?.preset ?? "outbound",
  }
}
